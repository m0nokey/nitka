ARG NITKA_BASE_IMAGE=alpine:3.23
FROM ${NITKA_BASE_IMAGE}

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=UTC

ARG NITKA_BASE_IMAGE
ARG NITKA_DOCKERFILE_SHA256
ARG NITKA_UID=1000

LABEL nitka.base-image="${NITKA_BASE_IMAGE}" \
      nitka.dockerfile-sha256="${NITKA_DOCKERFILE_SHA256}" \
      nitka.runtime-uid="${NITKA_UID}"

RUN apk add --no-cache \
    ansible-core \
    bash \
    curl \
    openssh-client \
    py3-pynacl \
    python3 \
    sshpass \
    tzdata

RUN cp /usr/share/zoneinfo/UTC /etc/localtime \
    && echo "UTC" > /etc/timezone

ENV ANSIBLE_COLLECTIONS_PATH=/usr/share/ansible/collections

RUN ansible-galaxy collection install community.docker \
    --collections-path /usr/share/ansible/collections

RUN addgroup -S user \
    && adduser -S -D -H -u "${NITKA_UID}" -G user -s /sbin/nologin user

WORKDIR /workspace
ENV HOME=/tmp
USER user
ENTRYPOINT ["/bin/bash", "/workspace/controller/entrypoint.sh"]
