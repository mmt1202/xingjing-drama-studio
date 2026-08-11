create table login_security_events (
    id uuid primary key,
    request_id varchar(80) not null,
    user_id uuid references users(id),
    identifier_hash char(64) not null,
    result varchar(16) not null,
    reason varchar(64) not null,
    ip_address varchar(64),
    region varchar(120),
    device_name varchar(160) not null,
    occurred_at timestamptz not null,
    constraint ck_login_security_event_result check (result in ('SUCCESS', 'FAILED'))
);

create index ix_login_security_events_time on login_security_events(occurred_at desc, id);
create index ix_login_security_events_user on login_security_events(user_id, occurred_at desc);

create function reject_login_security_event_mutation() returns trigger as $$
begin
    raise exception 'login_security_events are append-only';
end;
$$ language plpgsql;

create trigger trg_login_security_events_append_only
before update or delete on login_security_events
for each row execute function reject_login_security_event_mutation();
