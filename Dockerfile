FROM alpine:3.23

ARG NITKA_ANSIBLE_BASE_IMAGE_DIGEST=
ARG NITKA_ANSIBLE_DOCKERFILE_SHA256=

LABEL nitka.ansible.base-image="alpine:3.23" \
      nitka.ansible.base-digest="${NITKA_ANSIBLE_BASE_IMAGE_DIGEST}" \
      nitka.ansible.dockerfile-sha256="${NITKA_ANSIBLE_DOCKERFILE_SHA256}"

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=UTC

RUN apk add --no-cache \
    ansible \
    ansible-lint \
    bash \
    ca-certificates \
    coreutils \
    openssh-client \
    openssl \
    py3-cryptography \
    python3 \
    sshpass \
    tzdata \
    util-linux \
 && cp /usr/share/zoneinfo/UTC /etc/localtime \
 && echo "UTC" > /etc/timezone

WORKDIR /workspace/tcp

CMD ["bash"]
