# Global ARGs — available to all stages via empty redeclaration
# FFmpeg and libvmaf are commits of master: HDR-VMAF needs the built-in
# hdr_vmaf_v1.1.8 models (libvmaf 74f930d1) and the frame colorimetry that
# vf_libvmaf passes to libvmaf (FFmpeg 58584087cf), in no release of either yet
ARG FFMPEG_commit=88c46fce27e311c73772d9b83db4047dd83b1294
ARG VMAF_commit=a6ed0f18285cab504b779bbccb0bbaddc3bf44c2
ARG ZIMG_version=3.0.6
ARG EASYVMAF_VERSION=5.1.0

FROM python:3.12-slim AS base

FROM base AS build

ARG FFMPEG_commit
ARG VMAF_commit
ARG ZIMG_version

RUN export TZ='UTC' && \
    ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && \
    echo $TZ > /etc/timezone && \
    apt-get update -yqq && \
    apt-get install --no-install-recommends \
        ninja-build wget doxygen autoconf automake cmake \
        g++ gcc libdav1d-dev libtool pkg-config make nasm xxd yasm -y && \
    apt-get autoremove -y && \
    apt-get clean -y && \
    pip3 install --user meson

# Build zimg, which libvmaf converts the HDR pictures with. From source, at the
# same release in both images: its float results depend on how it was built, so
# distribution packages (3.0.5 in Debian, 3.0.3 in Ubuntu 22.04) could give the
# two images different HDR scores. The archive has no googletest submodule: only
# the unit tests, not built, need it
WORKDIR /tmp/zimg
RUN wget https://github.com/sekrit-twc/zimg/archive/refs/tags/release-${ZIMG_version}.tar.gz && \
    tar -xzf release-${ZIMG_version}.tar.gz && \
    cd zimg-release-${ZIMG_version} && \
    ./autogen.sh && \
    ./configure --prefix=/usr/local --libdir=/usr/local/lib --disable-static && \
    make -j$(nproc) && \
    make install && \
    ldconfig && \
    install -Dm644 COPYING /usr/local/share/licenses/zimg/COPYING && \
    rm -rf /tmp/zimg

# Build libvmaf
# --libdir=lib pins the install to /usr/local/lib on all platforms (meson defaults
# to lib64 on 64-bit Linux, lib on macOS — this makes both identical).
# -Denable_zimg=true: libvmaf converts the pictures to the HDR models'
# PQ/BT.2020/ICtCp with zimg, from the colorimetry FFmpeg passes
WORKDIR /tmp/vmaf
RUN wget https://github.com/Netflix/vmaf/archive/${VMAF_commit}.tar.gz && \
    tar -xzf ${VMAF_commit}.tar.gz && \
    cd vmaf-${VMAF_commit}/libvmaf/ && \
    export PATH="${HOME}/.local/bin:${PATH}" && \
    meson build --buildtype release -Dbuilt_in_models=true -Denable_zimg=true --libdir=lib && \
    ninja -vC build && \
    ninja -vC build install && \
    ldconfig && \
    mkdir -p /usr/local/share/model && \
    cp -R ../model/* /usr/local/share/model && \
    install -Dm644 ../LICENSE /usr/local/share/licenses/libvmaf/LICENSE && \
    rm -rf /tmp/vmaf

# Build FFmpeg with libvmaf
# Redistribution: the image carries the license texts under
# /usr/local/share/licenses, and ffmpeg/SOURCE says where FFmpeg's source code
# is (the LGPL requires it with the binaries): the archive built, its sha256 and
# its commit, which GitHub writes in the archive's pax header (the build stops
# if it is not the pinned one).
# A commit archive has no .git: FFmpeg would call itself "8.0.git" (master's
# RELEASE file). make REVISION= gives it the version of a shallow clone
# instead, git-<hash>, which check_ffmpeg() reads as a dev build
WORKDIR /tmp/ffmpeg
RUN export LD_LIBRARY_PATH="/usr/local/lib:${LD_LIBRARY_PATH}" && \
    export PKG_CONFIG_PATH="/usr/local/lib/pkgconfig:${PKG_CONFIG_PATH}" && \
    wget -O ffmpeg.tar.gz https://github.com/FFmpeg/FFmpeg/archive/${FFMPEG_commit}.tar.gz && \
    commit=$(python3 -c "import sys, tarfile; t = tarfile.open(sys.argv[1]); t.next(); print(t.pax_headers['comment'])" ffmpeg.tar.gz) && \
    { [ "${commit}" = "${FFMPEG_commit}" ] || { echo "FFmpeg archive is commit ${commit}, not ${FFMPEG_commit}"; exit 1; }; } && \
    sha256=$(sha256sum ffmpeg.tar.gz | cut -d' ' -f1) && \
    tar -xzf ffmpeg.tar.gz && \
    cd FFmpeg-${FFMPEG_commit} && \
    ./configure \
        --enable-libvmaf \
        --enable-version3 \
        --enable-shared \
        --enable-libdav1d && \
    grep -q '^#define HAVE_LIBVMAF_PICTURE_CONVERT 1$' config.h && \
    revision="git-$(echo ${FFMPEG_commit} | cut -c1-12)" && \
    make -j$(nproc) REVISION="${revision}" && \
    make install REVISION="${revision}" && \
    install -Dm644 -t /usr/local/share/licenses/ffmpeg COPYING.LGPLv3 COPYING.GPLv3 LICENSE.md && \
    printf '%s\n' \
        "FFmpeg ${revision} (master), LGPL-3.0-or-later (--enable-version3, without --enable-gpl or --enable-nonfree)." \
        "" \
        "Its source code, unmodified, is the archive this image was built from:" \
        "  https://github.com/FFmpeg/FFmpeg/archive/${FFMPEG_commit}.tar.gz" \
        "  sha256 ${sha256}" \
        "It is https://git.ffmpeg.org/ffmpeg.git at commit ${commit}." \
        "" \
        "It was configured and built by the Dockerfile of https://github.com/gdavila/easyVmaf" \
        "at the tag of this image's version (label org.opencontainers.image.version)." \
        "ffmpeg -buildconf prints its configure flags." \
        > /usr/local/share/licenses/ffmpeg/SOURCE && \
    rm -rf /tmp/ffmpeg

# Copy easyVmaf source for installation
WORKDIR /app
COPY . /app/easyvmaf/

FROM base AS release

ENV LD_LIBRARY_PATH="/usr/local/lib"

ARG FFMPEG_commit
ARG VMAF_commit
ARG ZIMG_version
ARG EASYVMAF_VERSION

LABEL org.opencontainers.image.title="easyVmaf"
LABEL org.opencontainers.image.version="${EASYVMAF_VERSION}"
LABEL org.opencontainers.image.description="FFmpeg-based VMAF computation with auto deinterlace, scale and sync"
# easyVmaf, FFmpeg (--enable-version3, no --enable-gpl), libvmaf, dav1d, zimg
LABEL org.opencontainers.image.licenses="MIT AND LGPL-3.0-or-later AND BSD-2-Clause-Patent AND BSD-2-Clause AND WTFPL"
LABEL org.opencontainers.image.source="https://github.com/gdavila/easyVmaf"
LABEL ffmpeg.commit="${FFMPEG_commit}"
LABEL libvmaf.commit="${VMAF_commit}"
LABEL zimg.version="${ZIMG_version}"

RUN export TZ='UTC' && \
    ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && \
    echo $TZ > /etc/timezone && \
    apt-get update -yqq && \
    apt-get install -y --no-install-recommends dav1d && \
    apt-get autoremove -y && \
    apt-get clean -y

COPY --from=build /usr/local /usr/local/
COPY --from=build /app/easyvmaf /app/easyvmaf/

# Dependencies from pyproject.toml, in the system site-packages: readable by
# any user (docker run -u), unlike pip --user, which installs in /root (0700)
WORKDIR /app/easyvmaf
RUN pip3 install --no-cache-dir --root-user-action=ignore .

ENTRYPOINT ["python3", "-u", "-m", "easyvmaf"]
