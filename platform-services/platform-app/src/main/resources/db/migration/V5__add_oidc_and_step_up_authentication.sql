create table external_identity_bindings (
    id uuid primary key,
    user_id uuid not null references users(id),
    provider_key varchar(80) not null,
    subject varchar(320) not null,
    email_at_binding varchar(320) not null,
    created_at timestamptz not null,
    created_by uuid not null references users(id),
    revoked_at timestamptz,
    revoked_by uuid references users(id),
    constraint uq_external_identity_subject unique (provider_key, subject)
);

create unique index uq_external_identity_active_user_provider
    on external_identity_bindings(user_id, provider_key)
    where revoked_at is null;

create table step_up_challenges (
    id uuid primary key,
    user_id uuid not null references users(id),
    session_id uuid not null references login_sessions(id),
    token_hash char(64) not null unique,
    purpose varchar(120) not null,
    expires_at timestamptz not null,
    consumed_at timestamptz,
    created_at timestamptz not null
);

create index ix_step_up_challenges_session
    on step_up_challenges(user_id, session_id, expires_at desc);
