alter table workspaces
    add column kind varchar(16) not null default 'PERSONAL',
    add constraint ck_workspaces_kind check (kind in ('PERSONAL', 'TEAM'));

create table workspace_command_receipts (
    actor_id uuid not null references users(id),
    action varchar(64) not null,
    idempotency_key varchar(160) not null,
    request_fingerprint char(64) not null,
    workspace_id uuid not null references workspaces(id),
    created_at timestamptz not null,
    primary key (actor_id, action, idempotency_key)
);
