"""One-way migration of legacy plaintext provider credentials to AES-GCM ciphertext."""

from __future__ import annotations

import argparse
import os

from sqlalchemy import create_engine, text

from lib.config.credential_crypto import encrypt_secret


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.environ.get("XINGJING_CREDENTIAL_DATABASE_URL", ""))
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or XINGJING_CREDENTIAL_DATABASE_URL is required")
    engine = create_engine(args.database_url, pool_pre_ping=True)
    migrated = 0
    with engine.begin() as connection:
        rows = connection.execute(
            text("SELECT id, api_key, access_key, secret_key FROM provider_credential FOR UPDATE")
        ).mappings()
        for row in rows:
            values = {}
            for field in ("api_key", "access_key", "secret_key"):
                value = row[field]
                if isinstance(value, str) and value and not value.startswith("enc:v1:"):
                    values[field] = encrypt_secret(value)
            if values:
                values["credential_id"] = row["id"]
                connection.execute(
                    text(
                        "UPDATE provider_credential SET api_key=COALESCE(:api_key,api_key), access_key=COALESCE(:access_key,access_key), secret_key=COALESCE(:secret_key,secret_key) WHERE id=:credential_id"
                    ),
                    {
                        "api_key": values.get("api_key"),
                        "access_key": values.get("access_key"),
                        "secret_key": values.get("secret_key"),
                        "credential_id": row["id"],
                    },
                )
                migrated += 1
    print(f"encrypted credential rows: {migrated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
