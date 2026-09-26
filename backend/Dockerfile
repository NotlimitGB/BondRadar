FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl libarchive-tools openssl \
    && rm -rf /var/lib/apt/lists/*

COPY certs/tbank/russian_trusted_root_ca.crt /usr/local/share/ca-certificates/russian_trusted_root_ca.crt
COPY certs/tbank/russian_trusted_sub_ca.crt /usr/local/share/ca-certificates/russian_trusted_sub_ca.crt
RUN update-ca-certificates \
    && test -s /etc/ssl/certs/ca-certificates.crt \
    && openssl verify -CAfile /usr/local/share/ca-certificates/russian_trusted_root_ca.crt /usr/local/share/ca-certificates/russian_trusted_root_ca.crt \
    && openssl verify -CAfile /usr/local/share/ca-certificates/russian_trusted_root_ca.crt /usr/local/share/ca-certificates/russian_trusted_sub_ca.crt \
    && openssl verify -CAfile /etc/ssl/certs/ca-certificates.crt /usr/local/share/ca-certificates/russian_trusted_root_ca.crt \
    && openssl verify -CAfile /etc/ssl/certs/ca-certificates.crt /usr/local/share/ca-certificates/russian_trusted_sub_ca.crt \
    && python -c "import ssl; c=ssl.create_default_context(cafile='/etc/ssl/certs/ca-certificates.crt'); assert c.verify_mode == ssl.CERT_REQUIRED and c.check_hostname; assert len(c.get_ca_certs()) > 50"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

