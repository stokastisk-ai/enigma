\set ON_ERROR_STOP on
-- the hosted verifier rolls back: no rows, no helper objects may remain
do $$ begin
  if (select count(*) from workspaces) <> 0 or (select count(*) from messages) <> 0 then raise exception 'verifier left fixture rows behind'; end if;
  if to_regclass('public.zz_results') is not null or to_regprocedure('public.zz_check(text,boolean)') is not null then raise exception 'verifier left helper objects behind'; end if;
end $$;
