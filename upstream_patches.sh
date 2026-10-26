#!/bin/bash -e

#Define variables
green='\033[0;32m'
red='\033[0;31m'
nocolor='\033[0m'
deps="git meson ninja patchelf unzip curl pip flex bison zip glslang glslangValidator"
workdir="$(pwd)/turnip_workdir"
base_workdir="$(pwd)"
ndkver="android-ndk-r29"
ndk="$workdir/$ndkver/toolchains/llvm/prebuilt/linux-x86_64/bin"
sdkver="34"
mesasrc="https://gitlab.freedesktop.org/mesa/mesa"
srcfolder="mesa"
srcdir="$workdir/$srcfolder"

#What to build on: a branch (main), a tag (mesa-26.3.0) or a commit SHA.
#Pin it for reproducible builds, e.g.: MESA_REF=<sha> BUILD_VERSION=12 ./build.sh
MESA_REF="${MESA_REF:-main}"
BUILD_VERSION="${BUILD_VERSION:-$(date +%Y%m%d)}"
#Only informational (goes into meta.json), bump when upstream's Vulkan version moves
vk_driver_version="Vulkan 1.4.363"

#Patch layout (git format-patch output or plain diffs):
#  patches/base/*.patch  - A8XX patches, applied to every build
#  patches/sync/*.patch  - extra patches, only applied to the "sync" build
#There are 3 steps here, simply comment to disable.
run_all(){
	echo "====== Begin building TU V$BUILD_VERSION! ======"
	echo "Current directory: $base_workdir"
	check_deps
	prepare_workdir
	setup_toolchain
	# $1 - variant id (build dir / install prefix), $2 - patch subdirs to apply (space separated)
	# $3 - output zip name (without .zip), $4 - name in meta.json
	build_lib_for_android a810 "base" "Turnip-A81X-V$BUILD_VERSION" "Turnip-New-A810-V$BUILD_VERSION"
	#build_lib_for_android a810-sync "base sync" "Turnip-A81X-sync-V$BUILD_VERSION" "Turnip-New-A810-sync-V$BUILD_VERSION"
}

check_deps(){
	echo "Checking system for required Dependencies ..."
		for deps_chk in $deps;
			do
				sleep 0.25
				if command -v "$deps_chk" >/dev/null 2>&1 ; then
					echo -e "$green - $deps_chk found $nocolor"
				else
					echo -e "$red - $deps_chk not found, can't countinue. $nocolor"
					deps_missing=1
				fi;
			done

		if [ "$deps_missing" == "1" ]
			then echo "Please install missing dependencies" && exit 1
		fi

	echo "Installing python Mako dependency (if missing) ..." $'\n'
		pip install mako &> /dev/null
}

prepare_workdir(){
	echo "Preparing work directory ..." $'\n'
		mkdir -p "$workdir" && cd "$workdir"

	if ! [ -d "$ndkver" ]; then
		echo "Downloading android-ndk from google server ..." $'\n'
			curl https://dl.google.com/android/repository/"$ndkver"-linux.zip --output "$ndkver"-linux.zip &> /dev/null
		echo "Exracting android-ndk ..." $'\n'
			unzip "$ndkver"-linux.zip &> /dev/null
	fi

	if [ -d "$srcdir/.git" ]; then
		echo "Updating mesa source ..." $'\n'
			git -C "$srcdir" fetch --tags origin
	else
		echo "Downloading mesa source ..." $'\n'
			#Blobless clone: full history, file contents fetched on demand.
			#Not shallow on purpose, "git am --3way" needs the old blobs when a patch has drifted.
			git clone --filter=blob:none --no-checkout "$mesasrc" "$srcdir"
	fi
	#git am needs an identity to create commits
	git -C "$srcdir" config user.name "turnip-build"
	git -C "$srcdir" config user.email "turnip-build@localhost"
}

setup_toolchain(){
	#Workaround for using Clang as c compiler instead of GCC
	mkdir -p "$workdir/bin"
	ln -sf "$ndk/clang" "$workdir/bin/cc"
	ln -sf "$ndk/clang++" "$workdir/bin/c++"
	export PATH="$workdir/bin:$ndk:$PATH"
	export CC=clang
	export CXX=clang++
	export AR=llvm-ar
	export RANLIB=llvm-ranlib
	export STRIP=llvm-strip
	export OBJDUMP=llvm-objdump
	export OBJCOPY=llvm-objcopy
	export LDFLAGS="-fuse-ld=lld"
}

# $1 - patch file. Fuzzy fallback for patches that drifted against current Mesa.
# Hunks that still fail are listed (and skipped), the rest is committed so later git am calls see a clean tree.
fuzzy_apply() {
	local p="$1"
	patch -p1 -N --fuzz=4 --no-backup-if-mismatch < "$p" || true
	if [ -n "$(find . -name '*.rej' -not -path './.git/*')" ]; then
		echo -e "$red Rejected hunks from $(basename "$p"):$nocolor"
		find . -name '*.rej' -not -path './.git/*'
		find . -name '*.rej' -not -path './.git/*' -delete
	fi
	if [ -n "$(git status --porcelain)" ]; then
		git add -A
		git commit -q -m "Apply $(basename "$p") (fuzzy)"
	fi
}

# $1 - patch subdir under patches/
apply_patch_dir() {
	local dir="$base_workdir/patches/$1"
	local count=0
	for p in "$dir"/*.patch "$dir"/*.diff; do
		[ -e "$p" ] || continue
		echo "Applying $1/$(basename "$p")"
		if head -n1 "$p" | grep -qE '^From[ :]'; then
			#format-patch output, keeps authorship when it applies cleanly
			if ! git am --3way --quiet "$p"; then
				git am --abort || true
				echo -e "$red git am failed for $p, falling back to fuzzy patch $nocolor"
				fuzzy_apply "$p"
			fi
		else
			#plain diff
			git apply --whitespace=nowarn "$p" || fuzzy_apply "$p"
		fi
		count=$((count + 1))
	done
	echo "Applied $count patch(es) from patches/$1"
}


# $1 - variant id, $2 - patch subdirs (space separated), $3 - output zip name (without .zip), $4 - name in meta.json
build_lib_for_android(){
	local variant="$1" patchsets="$2" zipname="$3" meta_name="$4"
	local builddir="$workdir/build-$variant"
	local prefix="/tmp/turnip-$variant"
	local ref

	cd "$srcdir"
	echo "==== Building Mesa ($MESA_REF) as $variant ===="
	if git rev-parse --verify -q "origin/$MESA_REF" >/dev/null; then
		ref="origin/$MESA_REF"
	else
		ref="$MESA_REF"
	fi
	git checkout --force --detach "$ref"
	git clean -fdq

	local mesaver srcsha
	mesaver="$(cat VERSION 2>/dev/null || echo unknown)"
	srcsha="$(git rev-parse --short HEAD)"
	echo "Mesa $mesaver @ $srcsha"

	for pset in $patchsets; do
		apply_patch_dir "$pset"
	done

	#Only matters if your patches reference tu_version.h, upstream doesn't have it
	echo "Pushing TU_VERSION..."
	echo "#define TUGEN8_DRV_VERSION \"v$BUILD_VERSION\"" > ./src/freedreno/vulkan/tu_version.h

	echo "Generating build files ..." $'\n'
		cat <<EOF >"$workdir/android-aarch64.txt"
[binaries]
ar = '$ndk/llvm-ar'
c = ['ccache', '$ndk/aarch64-linux-android$sdkver-clang']
cpp = ['ccache', '$ndk/aarch64-linux-android$sdkver-clang++', '-fno-exceptions', '-fno-unwind-tables', '-fno-asynchronous-unwind-tables', '--start-no-unused-arguments', '-static-libstdc++', '--end-no-unused-arguments']
c_ld = '$ndk/ld.lld'
cpp_ld = '$ndk/ld.lld'
strip = '$ndk/llvm-strip'
pkg-config = ['env', 'PKG_CONFIG_LIBDIR=$ndk/pkg-config', '/usr/bin/pkg-config']

[host_machine]
system = 'android'
cpu_family = 'aarch64'
cpu = 'armv8'
endian = 'little'
EOF

		cat <<EOF >"$workdir/native.txt"
[build_machine]
c = ['ccache', 'clang']
cpp = ['ccache', 'clang++']
ar = 'llvm-ar'
strip = 'llvm-strip'
c_ld = 'ld.lld'
cpp_ld = 'ld.lld'
system = 'linux'
cpu_family = 'x86_64'
cpu = 'x86_64'
endian = 'little'
EOF

		meson setup "$builddir" \
			--cross-file "$workdir/android-aarch64.txt" \
			--native-file "$workdir/native.txt" \
			--prefix "$prefix" \
			-Dbuildtype=release \
			-Dstrip=true \
			-Dplatforms=android \
			-Dvideo-codecs= \
			-Dandroid-stub=true \
			-Dgallium-drivers= \
			-Dvulkan-drivers=freedreno \
			-Dvulkan-beta=true \
			-Dfreedreno-kmds=kgsl \
			-Degl=disabled \
			-Dplatform-sdk-version=36 \
			-Dandroid-libbacktrace=disabled \
			--reconfigure

	echo "Compiling build files ..." $'\n'
		rm -rf "$prefix"
		ninja -C "$builddir" install

	if ! [ -f "$prefix/lib/libvulkan_freedreno.so" ]; then
		echo -e "$red Build failed! $nocolor" && exit 1
	fi

	echo "Making the archive"
	cat <<EOF >"$prefix/lib/meta.json"
{
  "schemaVersion": 1,
  "name": "$meta_name",
  "description": "Better A810/812 support. Upstream Mesa $mesaver ($srcsha) + patches ($patchsets).",
  "author": "WinterMist010",
  "packageVersion": "1",
  "vendor": "Mesa",
  "driverVersion": "$vk_driver_version",
  "minApi": 28,
  "libraryName": "libvulkan_freedreno.so"
}
EOF
	rm -f "/tmp/$zipname.zip"
	(cd "$prefix/lib" && zip "/tmp/$zipname.zip" libvulkan_freedreno.so meta.json)
	if ! [ -f "/tmp/$zipname.zip" ]; then
		echo -e "$red Failed to pack the archive! $nocolor" && exit 1
	fi
	echo -e "$green Done: /tmp/$zipname.zip $nocolor"
}

run_all
