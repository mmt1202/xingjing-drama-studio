create table account_command_receipts (
    user_id uuid not null references users(id),
    action varchar(64) not null,
    idempotency_key varchar(160) not null,
    request_fingerprint char(64) not null,
    created_at timestamptz not null,
    primary key (user_id, action, idempotency_key)
);
