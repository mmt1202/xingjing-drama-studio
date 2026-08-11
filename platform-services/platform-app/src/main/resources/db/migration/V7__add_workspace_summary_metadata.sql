alter table workspaces
    add column plan_code varchar(64);

update workspaces
set plan_code = case when kind = 'TEAM' then 'TEAM_FREE' else 'PERSONAL_FREE' end
where plan_code is null;

alter table workspaces
    alter column plan_code set not null;

alter table workspace_members
    add column last_selected_at timestamptz;

create index ix_workspace_members_user_recent
    on workspace_members (user_id, last_selected_at desc, workspace_id);
