FROM python:3.8-slim AS base

# setup dependencies versions

ARG	FFMPEG_version=master \
ARG	VMAF_version=master \
ARG	easyVmaf_hash=31c59a444445125265044789d0754db8f39f71be	

FROM base as build

# get and install building tools
RUN \
	export TZ='UTC' && \
	ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone && \
	apt-get update -yqq && \
	apt-get install --no-install-recommends\
		ninja-build \
		wget \
		doxygen \
		autoconf \
		automake \
		cmake \
		g++ \
		gcc \
		libdav1d-dev \
		pkg-config \
		make \
		nasm \	
		xxd \
		yasm -y && \
	apt-get autoremove -y && \
    apt-get clean -y && \
	pip3 install --user meson

# install libvmaf
WORKDIR     /tmp/vmaf
RUN \
	export PATH="${HOME}/.local/bin:${PATH}" && \
	echo $PATH &&\
	if [ "$VMAF_version" = "master" ] ; \
	 then wget https://github.com/Netflix/vmaf/archive/${VMAF_version}.tar.gz && \
	 tar -xzf  ${VMAF_version}.tar.gz ; \
	 else wget https://github.com/Netflix/vmaf/archive/v${VMAF_version}.tar.gz && \
	 tar -xzf  v${VMAF_version}.tar.gz ; \ 
	fi && \
	cd vmaf-${VMAF_version}/libvmaf/ && \
	meson build --buildtype release -Dbuilt_in_models=true && \
	ninja -vC build && \
	ninja -vC build test && \
	ninja -vC build install && \ 
	mkdir -p /usr/local/share/model  && \
	cp  -R ../model/* /usr/local/share/model && \
	rm -rf /tmp/vmaf

# install ffmpeg
WORKDIR     /tmp/ffmpeg
RUN \
	export LD_LIBRARY_PATH="${LD_LIBRARY_PATH}:/usr/local/lib/" && \
	export PKG_CONFIG_PATH="${PKG_CONFIG_PATH}:/usr/local/lib/pkgconfig/" && \
	wget https://github.com/FFmpeg/FFmpeg/archive/refs/heads/master.tar.gz  && \
	tar -xzf ${FFMPEG_version}.tar.gz && \
	cd FFmpeg-${FFMPEG_version} && \
	./configure --enable-libvmaf --enable-version3 --enable-shared --enable-libdav1d && \
	make -j4 && \
	make install && \
	rm -rf /tmp/ffmpeg

# install  easyVmaf
WORKDIR  /app
RUN \
	wget https://github.com/gdavila/easyVmaf/archive/${easyVmaf_hash}.tar.gz && \
	tar -xzf  ${easyVmaf_hash}.tar.gz

FROM base AS release

ENV LD_LIBRARY_PATH="${LD_LIBRARY_PATH}:/usr/local/lib/"

RUN \
	export TZ='UTC' && \
	ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone && \
	apt-get update -yqq && \
	apt-get install -y --no-install-recommends \
		dav1d && \
	apt-get autoremove -y && \
    apt-get clean -y && \
	pip3 install --user ffmpeg-progress-yield

COPY --from=build /usr/local /usr/local/
COPY --from=build /app/easyVmaf-${easyVmaf_hash} /app/easyVmaf/

# app setup
WORKDIR  /app/easyVmaf
ENTRYPOINT [ "python3", "-u", "easyVmaf.py" ]