# TLS certificates for GigaChat

## Bundle

`russian_trusted_root_ca.pem` contains:

1. Russian Trusted Root CA (НУЦ Минцифры)
2. Russian Trusted Sub CA

Both are required for the GigaChat API certificate chain observed via `openssl s_client`.

## Source

Downloaded 2026-10-03 from the URLs documented by Sber for GigaChat TLS:

- Root: https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt
- Sub: https://gu-st.ru/content/lending/russian_trusted_sub_ca_pem.crt

Reference: https://developers.sber.ru/docs/ru/gigachat/certificates

## Fingerprints (SHA-256)

| File | SHA-256 |
|------|---------|
| Root only | `936a43fea6e8e525bcc0f81acd9c3d21b4fc4b9b68acea7906d698005afc6504` |
| Sub only | `f0ae589f36774f29ef3648f7984b08d42fcce6f1ffeeb6236d773daeb2744ea6` |
| Bundle (`russian_trusted_root_ca.pem`) | `6e7fb02af0fdd48a67fc8d0cd391a200bf05797b2ac3ef5c62cce7e479d3530c` |

These are public CA certificates, not secrets.
