create table users (
    id uuid primary key,
    email varchar(320) not null,
    display_name varchar(120) not null,
    status varchar(32) not null,
    created_at timestamptz not null,
    created_by uuid not null,
    updated_at timestamptz not null,
    updated_by uuid not null,
    version bigint not null default 0,
    constraint uq_users_email unique (email),
    constraint ck_users_status check (status in ('ACTIVE', 'DISABLED', 'CANCELLED'))
);

create table workspaces (
    id uuid primary key,
    name varchar(160) not null,
    slug varchar(80) not null,
    status varchar(32) not null,
    created_at timestamptz not null,
    created_by uuid not null references users(id),
    updated_at timestamptz not null,
    updated_by uuid not null references users(id),
    version bigint not null default 0,
    constraint uq_workspaces_slug unique (slug),
    constraint ck_workspaces_status check (status in ('ACTIVE', 'SUSPENDED', 'CLOSED'))
);

create table workspace_members (
    workspace_id uuid not null references workspaces(id),
    user_id uuid not null references users(id),
    role_key varchar(80) not null,
    status varchar(32) not null,
    joined_at timestamptz not null,
    created_at timestamptz not null,
    created_by uuid not null references users(id),
    updated_at timestamptz not null,
    updated_by uuid not null references users(id),
    version bigint not null default 0,
    primary key (workspace_id, user_id),
    constraint ck_workspace_members_status check (status in ('INVITED', 'ACTIVE', 'SUSPENDED', 'REMOVED'))
);

create index ix_workspace_members_user on workspace_members(user_id, status);
