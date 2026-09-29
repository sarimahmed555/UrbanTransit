"""Explicit auth schema deployment and interactive account provisioning.

No default accounts, automatic migration or credentials in command arguments.
"""
import argparse
import getpass
import json
import re
import uuid
from pathlib import Path

from .config import SecurityConfig
from .contracts import Role
from .passwords import hash_password
from .postgres_repository import PostgresAuthRepository, postgres_config_from_dsn
from .service import USERNAME_PATTERN
from ..serving.database import PostgresDatabase


def migrate_auth(database):
    schema = Path(__file__).with_name('postgres_schema.sql')
    sql = '\n'.join(line for line in schema.read_text().splitlines() if not line.lstrip().startswith('--'))
    with database.transaction() as connection:
        cursor = connection.cursor()
        try:
            cursor.execute('CREATE SCHEMA IF NOT EXISTS app_auth')
            cursor.execute('CREATE TABLE IF NOT EXISTS app_auth.schema_migrations (migration_id text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())')
            cursor.execute('SELECT pg_advisory_xact_lock(782341901)')
            cursor.execute('SELECT 1 FROM app_auth.schema_migrations WHERE migration_id = %s', ('001_auth.sql',))
            if cursor.fetchone():
                return []
            # Existing contract installations must be inspected rather than silently relabelled.
            for statement in sql.split(';'):
                if statement.strip():
                    cursor.execute(statement)
            cursor.execute('INSERT INTO app_auth.schema_migrations (migration_id) VALUES (%s)', ('001_auth.sql',))
        finally:
            cursor.close()
    return ['001_auth.sql']


def create_user(database, username, role, password):
    username = username.lower()
    if not USERNAME_PATTERN.fullmatch(username):
        raise ValueError('Invalid username')
    role = Role(role)
    encoded = hash_password(password)
    user_id = str(uuid.uuid4())
    with database.transaction() as connection:
        cursor = connection.cursor()
        try:
            cursor.execute('INSERT INTO app_auth.users (user_id, username, password_hash, role) VALUES (%s, %s, %s, %s)',
                (user_id, username, encoded, role.value))
        finally:
            cursor.close()
    return user_id


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='operation',required=True)
    commands.add_parser('migrate'); commands.add_parser('readiness')
    user=commands.add_parser('create-user')
    user.add_argument('--username',required=True); user.add_argument('--role',choices=[r.value for r in Role],required=True)
    args=parser.parse_args(argv)
    try:
        config=SecurityConfig.from_env()
        if not config.database_dsn:
            print(json.dumps({'status':'NOT_CONFIGURED','component':'authentication'})); return 2
        database=PostgresDatabase(postgres_config_from_dsn(config.database_dsn))
        if args.operation=='migrate':
            print(json.dumps({'status':'ready','applied':migrate_auth(database)}))
        elif args.operation=='readiness':
            state=PostgresAuthRepository(database).readiness(); print(json.dumps(state)); return 0 if state['state']=='READY' else 2
        else:
            password=getpass.getpass('New account password: ')
            if password != getpass.getpass('Confirm password: '):
                print(json.dumps({'status':'error','message':'Passwords did not match'})); return 2
            identifier=create_user(database,args.username,args.role,password)
            print(json.dumps({'status':'created','user_id':identifier}))
        return 0
    except Exception:
        print(json.dumps({'status':'NOT_READY','message':'Authentication operation failed. Check database configuration, schema and account input.'})); return 2


if __name__=='__main__':
    raise SystemExit(main())
