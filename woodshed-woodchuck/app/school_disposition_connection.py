"""Fixed, independently approved external transport for the supervised C38 operator."""
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import fcntl
from hashlib import sha256
from ipaddress import ip_address
import os
from pathlib import Path
import re
import secrets
from tempfile import NamedTemporaryFile

import psycopg
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from .classroom_role_membership import UNEXPECTED_MEMBERSHIP
from .support_case_files import SupportDenied, _digest, _protected_file, _read_json, _reference, _time

EXECUTOR = "disposition_custodian"
WITNESS = "c37_disposition_witness"
VERIFIER = "disposition_verifier"
ROLES = (EXECUTOR, WITNESS, VERIFIER)
REVISION = "c38disp001"


def require(condition, reason):
    if not condition:
        raise SupportDenied(reason)


def role_grants(conn):
    """Application ACLs and explicit system-routine grants for this membership-free login."""
    return conn.execute("""
        WITH objects(kind,name,acl) AS (
            SELECT c.relkind::text, format('%I.%I',n.nspname,c.relname), c.relacl
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema'
            UNION ALL
            SELECT 'column',format('%I.%I.%I',n.nspname,c.relname,a.attname),a.attacl
            FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE a.attacl IS NOT NULL AND n.nspname !~ '^pg_' AND n.nspname<>'information_schema'
            UNION ALL
            SELECT CASE WHEN p.prosecdef THEN 'definer' ELSE 'routine' END,
                p.oid::regprocedure::text, coalesce(p.proacl,acldefault('f',p.proowner))
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE (n.nspname !~ '^pg_' AND n.nspname<>'information_schema')
               OR EXISTS (SELECT FROM aclexplode(p.proacl) acl WHERE acl.grantee=current_user::regrole)
        )
        SELECT kind,name,privilege_type,is_grantable FROM objects CROSS JOIN LATERAL aclexplode(acl)
        WHERE grantee IN (0,current_user::regrole) ORDER BY kind COLLATE "C",name COLLATE "C",privilege_type COLLATE "C",is_grantable
        """).fetchall()


def validate_role(conn, role, expected):
    safe = conn.execute(f"""
        SELECT rolcanlogin AND NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls
            OR {UNEXPECTED_MEMBERSHIP}
            OR has_database_privilege(r.oid,current_database(),'CREATE')
            OR has_parameter_privilege(r.oid,'session_replication_role','SET')
            OR EXISTS(SELECT FROM pg_namespace n WHERE n.nspname !~ '^pg_temp_' AND has_schema_privilege(r.oid,n.oid,'CREATE'))
            OR EXISTS(SELECT FROM pg_shdepend s WHERE s.refclassid='pg_authid'::regclass
               AND s.refobjid=r.oid AND s.deptype='o' AND (s.dbid=0 OR s.dbid=
                 (SELECT oid FROM pg_database WHERE datname=current_database()))))
        FROM pg_roles r WHERE rolname=current_user
        """).fetchone()
    require(safe == (True,), "disposition_external_privileges")
    grants = role_grants(conn)
    require(_digest(grants) == expected and not any(row[3] for row in grants),
            "disposition_external_acl_changed")
    if role != EXECUTOR:
        require(all(priv in ("SELECT","EXECUTE") and kind != "definer" for kind, _, priv, _ in grants),
                "disposition_external_readonly_required")


class ExternalTarget:
    def __init__(self, config, approved, ca_path):
        self.ca_path = ca_path
        self.passwords = {}
        for role in ROLES:
            with _protected_file(Path(config["credentials"][role]), config["service_uid"]) as stream:
                raw = stream.read(4098)
            password = raw.removesuffix(b"\n")
            require(0 < len(password) <= 4096 and not any(c in password for c in (b"\n", b"\r", b"\0")),
                    "disposition_external_credential")
            self.passwords[role] = password.decode()
        require(len(set(self.passwords.values())) == len(ROLES), "disposition_external_credential_separation")
        self.target = config["external"]
        self.binding = _digest(self.target)
        self.until = _time(approved["until"])
        self.anchor = None
        self.lock = (secrets.randbits(31), secrets.randbits(31))

    def check_time(self):
        require(datetime.now(timezone.utc) < self.until, "disposition_destination_expired")

    def connect(self, role):
        require(role in ROLES, "disposition_external_role")
        self.check_time()
        t = self.target
        # Only these explicit libpq parameters cross the privileged boundary.
        params = dict(host=t["host"], port=t["port"], dbname=t["database"], user=role, password=self.passwords[role],
                      sslmode="verify-full", sslrootcert=self.ca_path, sslcertmode="disable",
                      gssencmode="disable", require_auth="scram-sha-256", channel_binding="require",
                      ssl_min_protocol_version="TLSv1.2", connect_timeout=5, application_name="ww-disposition",
                      options="-c search_path=pg_catalog,public,pg_temp -c row_security=off" +
                              (" -c default_transaction_read_only=on" if role != EXECUTOR else ""))
        if t.get("address"):
            params["hostaddr"] = str(ip_address(t["address"]))
        conn = None
        try:
            conn = psycopg.connect(**params, autocommit=True, prepare_threshold=None)
            self.validate(conn, role)
            conn.autocommit = False
            return conn
        except BaseException as error:
            if conn is not None:
                conn.close()
            if isinstance(error, psycopg.Error):
                raise SupportDenied("disposition_external_connection_unavailable") from None
            raise

    def validate(self, conn, role):
        self.check_time()
        t = self.target
        identity = conn.execute("""
            SELECT current_database(),d.oid,session_user,current_user,
              (SELECT system_identifier::text FROM pg_control_system()),pg_is_in_recovery(),
              inet_server_addr() IS NOT NULL, ssl, current_setting('transaction_read_only'), pg_backend_pid(), host(inet_client_addr())
            FROM pg_database d JOIN pg_stat_ssl s ON s.pid=pg_backend_pid() WHERE d.datname=current_database()
            """).fetchone()
        require(conn.pgconn.ssl_in_use and identity and identity[9:] == (conn.info.backend_pid, t["egress"])
                and identity[:8] == (
            t["database"],t["oid"],role,role,t["system_id"],False,True,True),
            "disposition_external_target_mismatch")
        if role != EXECUTOR:
            require(identity[8] == "on", "disposition_external_readonly_required")
        validate_role(conn, role, t["role_acls"][role])
        if self.anchor is not None:
            # This random lock exists only on the approved live primary during this operation.
            same = conn.execute("""
                SELECT EXISTS(SELECT FROM pg_locks WHERE locktype='advisory' AND granted
                    AND database=%s AND classid=%s AND objid=%s AND objsubid=2 AND pid=%s)
                """, (t["oid"], *self.lock, self.anchor.info.backend_pid)).fetchone()
            require(same == (True,), "disposition_external_target_split")
            self.check_revision()

    def check_revision(self):
        require(self.anchor.execute("SELECT version_num FROM public.alembic_version").fetchall() == [(REVISION,)],
                "disposition_revision_mismatch")

    def engine(self, role):
        return create_engine("postgresql+psycopg://", poolclass=NullPool, hide_parameters=True,
                             creator=lambda: self.connect(role))


@contextmanager
def approved_target(config):
    from .school_disposition import DESTINATION_APPROVAL, verify_approval

    require(not any(k.startswith(("PG","C37_")) or k in ("DATABASE_URL","OPENSSL_CONF","OPENSSL_MODULES",
            "SSL_CERT_FILE","SSL_CERT_DIR","SSLKEYLOGFILE") for k in os.environ), "disposition_environment_unqualified")
    require(set(config) == {"external","database","credentials","ca_file","role","owner_uid","service_uid",
                            "signers","signers_sha256","register","approvals","ordinary_roles","pilot_program"},
            "disposition_external_configuration")
    t = config["external"]
    keys = {"host","port","egress","database","oid","resource","generation","system_id","ca_sha256","role_acls"}
    require(isinstance(t,dict) and set(t) in (keys,keys|{"address"}) and "socket" not in config
            and config["role"] == EXECUTOR and config["database"] == t["database"]
            and set(config["credentials"]) == set(ROLES) and len(set(config["credentials"].values())) == 3,
            "disposition_external_configuration")
    require(isinstance(t["host"],str) and len(t["host"]) <= 253 and "." in t["host"] and re.search("[a-z]", t["host"])
            and all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in t["host"].split("."))
            and type(t["port"]) is int and 0 < t["port"] <= 65535
            and re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,62}",t["database"])
            and type(t["oid"]) is int and t["oid"] > 0 and re.fullmatch(r"[1-9][0-9]{0,19}",t["system_id"])
            and _reference(t["resource"]) and _reference(t["generation"])
            and set(t["role_acls"]) == set(ROLES)
            and all(re.fullmatch("[0-9a-f]{64}",v) for v in [t["ca_sha256"],*t["role_acls"].values()]),
            "disposition_external_configuration")
    # One exact approved OPS egress address. Reject networks, hostnames and address lists.
    for key in ("egress", "address"):
        if key in t:
            try:
                require(isinstance(t[key], str) and str(ip_address(t[key])) == t[key] and "%" not in t[key],
                        "disposition_external_address")
            except ValueError:
                raise SupportDenied("disposition_external_address") from None
    directory = Path(config["approvals"])
    with ExitStack() as stack:
        lock = stack.enter_context(_protected_file(directory/"destination.lock", config["owner_uid"]))
        fcntl.flock(lock, fcntl.LOCK_SH)
        with _protected_file(directory/"destination.json", config["owner_uid"]) as stream:
            approval = _read_json(stream)
        require(isinstance(approval, dict)
                and set(approval) == {"case","decision","target","since","until","approval_contract","pilot_program"}
                and type(config["pilot_program"]) is int and config["pilot_program"] > 0
                and type(approval["pilot_program"]) is int and approval["pilot_program"] == config["pilot_program"]
                and approval["case"]=="DESTINATION"
                and approval["decision"]=="approved_render_direct_external" and approval["target"]==t
                and _time(approval["since"]) <= datetime.now(timezone.utc) < _time(approval["until"]),
                "disposition_destination_unapproved")
        verify_approval(config, approval, directory, DESTINATION_APPROVAL)
        ca = stack.enter_context(NamedTemporaryFile(dir=config["register"]))
        with _protected_file(Path(config["ca_file"]),config["owner_uid"]) as source:
            data = source.read(1024*1024+1)
        require(len(data) <= 1024*1024 and sha256(data).hexdigest() == t["ca_sha256"], "disposition_external_ca_changed")
        ca.write(data)
        ca.flush()
        target = ExternalTarget(config,approval,ca.name)
        conn = stack.enter_context(target.connect(EXECUTOR))
        conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ ONLY")
        require(conn.execute("SELECT pg_try_advisory_xact_lock(%s::int,%s::int)",target.lock).fetchone()==(True,),
                "disposition_external_anchor_unavailable")
        target.anchor = conn
        target.check_revision()
        for role in (WITNESS,VERIFIER):
            with target.connect(role):
                pass
        yield target
