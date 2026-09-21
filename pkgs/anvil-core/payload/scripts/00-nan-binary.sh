#!/bin/sh

###busybox devmem 0x00a130d1 8 1

VERSION_DIR=$(ls /usr/prog/PROGRAM/kernel/ 2>/dev/null | head -n 1)

case "$VERSION_DIR" in
    2.0.1|2.0.2|2.0.3|2.0.4|2.0.5|2.0.6)
        busybox devmem 0x00a130d1 8 1
        ;;
    2.0.7)
        busybox devmem 0x00B330D1 8 1
        ;;
    *)
        echo "Error: Unrecognized or missing version directory ('$VERSION_DIR')" >&2
        exit 1
        ;;
esac