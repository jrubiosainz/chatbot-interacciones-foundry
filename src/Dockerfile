# Imagen base de MCR (evita los límites de descarga de Docker Hub en ACR Tasks).
FROM mcr.microsoft.com/azurelinux/base/python:3.12

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    PIP_BREAK_SYSTEM_PACKAGES=1

WORKDIR /srv

RUN python3 -m pip --version >/dev/null 2>&1 || (tdnf install -y python3-pip && tdnf clean all)

COPY requirements.txt .
RUN python3 -m pip install -r requirements.txt

COPY app ./app

EXPOSE 80
CMD ["python3", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "80", "--proxy-headers", "--forwarded-allow-ips", "*"]
