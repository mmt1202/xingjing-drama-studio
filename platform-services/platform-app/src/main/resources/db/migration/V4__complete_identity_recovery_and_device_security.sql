alter table login_sessions
    add column risk_status varchar(16) not null default 'NORMAL',
    add constraint ck_login_sessions_risk_status
        check (risk_status in ('NORMAL', 'SUSPICIOUS', 'BLOCKED'));

create table password_reset_challenges (
    id uuid primary key,
    user_id uuid not null references users(id),
    token_hash char(64) not null,
    expires_at timestamptz not null,
    consumed_at timestamptz,
    requested_ip varchar(64),
    created_at timestamptz not null,
    constraint uq_password_reset_challenges_token unique (token_hash)
);

create index ix_password_reset_challenges_user
    on password_reset_challenges(user_id, created_at desc);

create table identity_delivery_audit (
    id uuid primary key,
    request_id varchar(80) not null,
    user_id uuid references users(id),
    channel varchar(32) not null,
    template_key varchar(80) not null,
    outcome varchar(16) not null,
    provider_message_id varchar(160),
    occurred_at timestamptz not null,
    constraint ck_identity_delivery_audit_outcome
        check (outcome in ('SENT', 'FAILED'))
);

create index ix_identity_delivery_audit_request
    on identity_delivery_audit(request_id, occurred_at desc);

create function reject_identity_delivery_audit_mutation() returns trigger as $$
begin
    raise exception 'identity delivery audit is append-only';
end;
$$ language plpgsql;

create trigger trg_identity_delivery_audit_append_only
before update or delete on identity_delivery_audit
for each row execute function reject_identity_delivery_audit_mutation();
