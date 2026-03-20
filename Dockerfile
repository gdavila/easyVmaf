# Global ARGs — available to all stages via empty redeclaration
ARG FFMPEG_version=8.1
ARG VMAF_version=3.0.0
ARG EASYVMAF_VERSION=2.1.0

FROM python:3.12-slim AS base

FROM base AS build

ARG FFMPEG_version
ARG VMAF_version

RUN export TZ='UTC' && \
    ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && \
    echo $TZ > /etc/timezone && \
    apt-get update -yqq && \
    apt-get install --no-install-recommends \
        ninja-build wget doxygen autoconf automake cmake \
        g++ gcc libdav1d-dev pkg-config make nasm xxd yasm -y && \
    apt-get autoremove -y && \
    apt-get clean -y && \
    pip3 install --user meson

# Build libvmaf
WORKDIR /tmp/vmaf
RUN wget https://github.com/Netflix/vmaf/archive/v${VMAF_version}.tar.gz && \
    tar -xzf v${VMAF_version}.tar.gz && \
    cd vmaf-${VMAF_version}/libvmaf/ && \
    export PATH="${HOME}/.local/bin:${PATH}" && \
    meson build --buildtype release -Dbuilt_in_models=true && \
    ninja -vC build && \
    ninja -vC build install && \
    ldconfig && \
    mkdir -p /usr/local/share/model && \
    cp -R ../model/* /usr/local/share/model && \
    rm -rf /tmp/vmaf

# Diagnose libvmaf install layout
RUN echo "=== find .pc files ===" && \
    find /usr/local -name "*.pc" 2>/dev/null && \
    echo "=== find libvmaf.so* ===" && \
    find /usr/local -name "libvmaf*" 2>/dev/null && \
    echo "=== pkg-config default search path ===" && \
    pkg-config --variable pc_path pkg-config && \
    echo "=== PKG_CONFIG_PATH env ===" && \
    echo "${PKG_CONFIG_PATH}" && \
    echo "=== pkg-config libvmaf (no extra path) ===" && \
    pkg-config --libs libvmaf 2>&1 || true && \
    echo "=== pkg-config libvmaf (with /usr/local paths) ===" && \
    PKG_CONFIG_PATH="/usr/local/lib/pkgconfig:/usr/local/lib/x86_64-linux-gnu/pkgconfig" \
    pkg-config --libs libvmaf 2>&1 || true

# Build FFmpeg with libvmaf
WORKDIR /tmp/ffmpeg
RUN export LD_LIBRARY_PATH="${LD_LIBRARY_PATH}:/usr/local/lib/" && \
    export PKG_CONFIG_PATH="/usr/local/lib/pkgconfig:/usr/local/lib/x86_64-linux-gnu/pkgconfig:${PKG_CONFIG_PATH}" && \
    wget https://github.com/FFmpeg/FFmpeg/archive/refs/tags/n${FFMPEG_version}.tar.gz && \
    tar -xzf n${FFMPEG_version}.tar.gz && \
    cd FFmpeg-n${FFMPEG_version} && \
    ./configure \
        --enable-libvmaf \
        --enable-version3 \
        --enable-shared \
        --enable-libdav1d && \
    make -j$(nproc) && \
    make install && \
    rm -rf /tmp/ffmpeg

# Copy easyVmaf source for installation
WORKDIR /app
COPY . /app/easyvmaf/

FROM base AS release

ENV LD_LIBRARY_PATH="${LD_LIBRARY_PATH}:/usr/local/lib/"

ARG FFMPEG_version
ARG VMAF_version
ARG EASYVMAF_VERSION

LABEL org.opencontainers.image.title="easyVmaf"
LABEL org.opencontainers.image.version="${EASYVMAF_VERSION}"
LABEL org.opencontainers.image.description="FFmpeg-based VMAF computation with auto deinterlace, scale and sync"
LABEL org.opencontainers.image.licenses="MIT"
LABEL ffmpeg.version="${FFMPEG_version}"
LABEL libvmaf.version="${VMAF_version}"

RUN export TZ='UTC' && \
    ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && \
    echo $TZ > /etc/timezone && \
    apt-get update -yqq && \
    apt-get install -y --no-install-recommends dav1d && \
    apt-get autoremove -y && \
    apt-get clean -y && \
    pip3 install --user ffmpeg-progress-yield

COPY --from=build /usr/local /usr/local/
COPY --from=build /app/easyvmaf /app/easyvmaf/

WORKDIR /app/easyvmaf
RUN pip3 install --no-deps .

ENTRYPOINT ["python3", "-u", "-m", "easyvmaf"]
