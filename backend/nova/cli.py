"""Operator commands:  flask --app app <command>

  create-admin   create or promote an administrator (the safe way to bootstrap production)
  migrate        bring the database schema up to date
  maintenance    run cleanup and retention now
  backup         write a consistent copy of the database (SQLite) to a file
  restore        replace the SQLite database with a backup (stop the server first)
  check-config   validate settings for the current APP_ENV and exit
  schema-sql     print the database schema as SQL for a dialect (reference / DBA review; `migrate` applies it)
  workspace-jobs run due workspace jobs once (for cron, when WORKSPACE_JOBS=false)
  channels       show WhatsApp / Teams / email status;  channels --poll-email reads the mailbox once (for cron)
"""
import getpass
import uuid

import click
from sqlalchemy import select, update

from . import backup as backup_mod
from . import db, security
from .config import settings
from .maintenance import run_maintenance
from .users import adopt_orphan_chats


def register(app):
    @app.cli.command("create-admin")
    @click.option("--email", required=True)
    @click.option("--name", default="Administrator")
    @click.option("--password", default=None, help="Omit to be prompted (recommended).")
    def create_admin(email, name, password):
        """Create an administrator, or promote an existing account."""
        email = email.strip().lower()
        with db.engine.begin() as c:
            u = c.execute(select(db.users).where(db.users.c.email == email)).first()
            first_admin = not c.execute(select(db.users.c.id).where(db.users.c.role == "admin")).first()
            if u:
                c.execute(update(db.users).where(db.users.c.id == u.id).values(role="admin", disabled=0, email_verified=1))
                uid = u.id
                click.echo(f"Promoted {email} to administrator.")
            else:
                pw = password or getpass.getpass("Password for the new administrator: ")
                security.check_password_policy(pw, email)
                uid = str(uuid.uuid4())
                now = db.now_us()
                c.execute(db.users.insert().values(id=uid, email=email, name=name[:100], password_hash=security.hash_password(pw),
                                                   role="admin", disabled=0, email_verified=1, created_at=now,
                                                   password_changed_at=now))
                click.echo(f"Created administrator {email}.")
            if first_admin:
                adopt_orphan_chats(c, uid)
        security.audit("admin.created_by_cli", actor_id=None, target_type="user", target_id=uid)

    @app.cli.command("migrate")
    def migrate():
        """Apply schema migrations."""
        out = db.migrate()
        click.echo(f"Schema version {db.schema_version()} ({out})")

    @app.cli.command("schema-sql")
    @click.option("--dialect", type=click.Choice(["postgresql", "mysql", "sqlite"]), default="postgresql")
    def schema_sql(dialect):
        """Print CREATE TABLE / CREATE INDEX statements for the whole schema."""
        click.echo(db.schema_sql(dialect))

    @app.cli.command("maintenance")
    def maintenance():
        """Delete expired sessions, approvals and data past the retention limits."""
        click.echo(run_maintenance())

    @app.cli.command("backup")
    @click.argument("destination", required=False)
    def backup(destination):
        """Write a database backup (SQLite) and verify it."""
        path = backup_mod.backup(settings.DATABASE_URL, destination)
        click.echo(f"Backup written and verified: {path}")

    @app.cli.command("restore")
    @click.argument("source")
    @click.option("--yes", is_flag=True, help="Do not ask for confirmation.")
    def restore(source, yes):
        """Restore the SQLite database from a backup file (stop the app first)."""
        if not yes:
            click.confirm(f"Replace the current database with {source}?", abort=True)
        backup_mod.restore(settings.DATABASE_URL, source)
        click.echo("Restored. Start the app; migrations will run automatically.")

    @app.cli.command("check-config")
    def check_config():
        """Print configuration problems for this APP_ENV."""
        for w in settings.warnings:
            click.echo(f"warning: {w}")
        for e in settings.errors:
            click.echo(f"error: {e}")
        click.echo("OK" if not settings.errors else "Configuration has errors.")

    @app.cli.command("channels")
    @click.option("--poll-email", is_flag=True, help="Read and answer unread email once, then exit.")
    def channels(poll_email):
        """Show which chat channels are configured, or poll the email channel once."""
        from .channels import core
        from .channels.email_channel import poll_once
        if poll_email:
            if not settings.EMAIL_CHANNEL_ENABLED:
                raise click.ClickException("The email channel is not configured (EMAIL_CHANNEL_IMAP_HOST/USER/PASSWORD).")
            click.echo(f"Handled {poll_once()} message(s).")
            return
        base = settings.APP_URL or "<APP_URL>"
        hooks = {"whatsapp": f"{base}/api/channels/whatsapp/webhook", "teams": f"{base}/api/channels/teams/messages",
                 "email": settings.EMAIL_CHANNEL_ADDRESS}
        for ch in core.CHANNELS:
            click.echo(f"{core.LABELS[ch]:16} {'enabled ' if core.enabled(ch) else 'disabled'}  {hooks[ch] or ''}")

    @app.cli.command("workspace-jobs")
    @click.option("--run-due", is_flag=True, help="Run every job that is due now, then exit.")
    def workspace_jobs(run_due):
        """Run due workspace jobs (briefings, reminders, follow-ups…) once."""
        from .workspace import jobs
        if not run_due:
            raise click.ClickException("Use --run-due (schedule it every minute with cron or a systemd timer).")
        click.echo(f"Ran {jobs.run_due('cli')} job(s).")
