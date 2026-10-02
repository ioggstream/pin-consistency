FROM --platform=linux/amd64 python:3.13@sha256:1111111111111111111111111111111111111111111111111111111111111111 AS build
FROM build
FROM scratch
COPY --from=build /a /a
COPY --from=0 /a /a
COPY --from=busybox:1 /bin/sh /sh
