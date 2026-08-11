create table user_credentials (
    id uuid primary key,
    user_id uuid not null references users(id),
    identifier_hash char(64) not null,
    password_hash varchar(100) not null,
    failed_count integer not null default 0,
    locked_until timestamptz,
    created_at timestamptz not null,
    updated_at timestamptz not null,
    version bigint not null default 0,
    constraint uq_user_credentials_identifier_hash unique (identifier_hash),
    constraint ck_user_credentials_failed_count check (failed_count >= 0)
);

create index ix_user_credentials_user on user_credentials(user_id);

create table login_sessions (
    id uuid primary key,
    user_id uuid not null references users(id),
    token_hash char(64) not null,
    refresh_token_hash char(64) not null,
    device_name varchar(160) not null,
    expires_at timestamptz not null,
    refresh_expires_at timestamptz not null,
    current_workspace_id uuid references workspaces(id),
    revoked_at timestamptz,
    revoke_reason varchar(80),
    replaced_by_session_id uuid references login_sessions(id),
    last_seen_at timestamptz not null,
    created_at timestamptz not null,
    constraint uq_login_sessions_token_hash unique (token_hash),
    constraint uq_login_sessions_refresh_token_hash unique (refresh_token_hash)
);

create index ix_login_sessions_user_expiry on login_sessions(user_id, expires_at desc);

create table session_workspace_selections (
    session_id uuid not null references login_sessions(id),
    idempotency_key varchar(160) not null,
    workspace_id uuid not null references workspaces(id),
    request_id varchar(80) not null,
    selected_at timestamptz not null,
    constraint pk_session_workspace_selections primary key (session_id, idempotency_key)
);

create table roles (
    id uuid primary key,
    workspace_id uuid not null references workspaces(id),
    code varchar(80) not null,
    name varchar(120) not null,
    data_scope varchar(32) not null,
    is_system boolean not null default false,
    created_at timestamptz not null,
    created_by uuid not null references users(id),
    updated_at timestamptz not null,
    updated_by uuid not null references users(id),
    version bigint not null default 0,
    constraint uq_roles_workspace_code unique (workspace_id, code),
    constraint ck_roles_data_scope check (data_scope in ('SELF', 'ASSIGNED_OBJECTS', 'PROJECTS', 'WORKSPACE'))
);

create table permissions (
    id uuid primary key,
    code varchar(120) not null,
    resource varchar(80) not null,
    action varchar(80) not null,
    risk_level varchar(16) not null,
    constraint uq_permissions_code unique (code),
    constraint ck_permissions_risk_level check (risk_level in ('LOW', 'MEDIUM', 'HIGH'))
);

create table role_permissions (
    role_id uuid not null references roles(id),
    permission_id uuid not null references permissions(id),
    constraint pk_role_permissions primary key (role_id, permission_id)
);

create table member_roles (
    workspace_id uuid not null,
    user_id uuid not null,
    role_id uuid not null references roles(id),
    assigned_at timestamptz not null,
    assigned_by uuid not null references users(id),
    constraint pk_member_roles primary key (workspace_id, user_id, role_id),
    constraint fk_member_roles_member foreign key (workspace_id, user_id)
        references workspace_members(workspace_id, user_id)
);

create table audit_logs (
    id uuid primary key,
    request_id varchar(80) not null,
    actor_id uuid references users(id),
    workspace_id uuid references workspaces(id),
    action varchar(160) not null,
    target_type varchar(120) not null,
    target_id uuid,
    before_digest char(64),
    after_digest char(64),
    result varchar(32) not null,
    occurred_at timestamptz not null,
    constraint ck_audit_logs_result check (result in ('SUCCESS', 'DENIED', 'FAILED'))
);

create index ix_audit_logs_request on audit_logs(request_id, occurred_at desc);
create index ix_audit_logs_workspace on audit_logs(workspace_id, occurred_at desc);
create index ix_audit_logs_actor on audit_logs(actor_id, occurred_at desc);

create function reject_audit_log_mutation() returns trigger as $$
begin
    raise exception 'audit_logs are append-only';
end;
$$ language plpgsql;

create trigger trg_audit_logs_append_only
before update or delete on audit_logs
for each row execute function reject_audit_log_mutation();
