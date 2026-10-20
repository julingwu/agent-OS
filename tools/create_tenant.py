"""Tenant provisioning CLI: creates tenant, root legal entity, and CEO identity.

Usage:
    python tools/create_tenant.py --id TENANT-BETA --name "贝塔科技" [--entity LE-BETA-HQ] [--admin-name beta-admin]
    python tools/create_tenant.py --list
"""
from __future__ import annotations

import argparse
import pathlib
import sqlite3
import sys

# Ensure repository root is on sys.path
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import audit, identity, tenancy  # noqa: E402
from app.db import connect, init_db  # noqa: E402


def list_tenants(conn: sqlite3.Connection) -> int:
    """List all tenants with their legal entity and identity counts."""
    tenancy.ensure(conn)
    rows = conn.execute("""
        SELECT
            t.id,
            t.name,
            t.plan,
            t.state,
            t.created_at,
            COUNT(DISTINCT le.id) AS entity_count,
            COUNT(DISTINCT i.id) AS identity_count
        FROM tenants t
        LEFT JOIN legal_entities le ON COALESCE(le.tenant_id, 'TENANT-HQ') = t.id
        LEFT JOIN identities i ON COALESCE(i.tenant_id, 'TENANT-HQ') = t.id
        GROUP BY t.id
        ORDER BY t.created_at
    """).fetchall()

    if not rows:
        print("未找到任何租户。")
        return 0

    print(f"{'租户 ID':<20} {'租户名称':<20} {'实体数':<8} {'身份数':<8} {'状态':<10} {'创建时间'}")
    print("-" * 80)
    for r in rows:
        created = str(r["created_at"] or "")[:19]
        print(f"{r['id']:<20} {r['name']:<20} {r['entity_count']:<8} {r['identity_count']:<8} {r['state']:<10} {created}")
    return 0


def create_tenant(
    conn: sqlite3.Connection,
    *,
    tenant_id: str,
    name: str,
    entity_id: str | None = None,
    admin_name: str | None = None,
) -> dict:
    """Create a new tenant with its root legal entity and CEO identity idempotently."""
    tenancy.ensure(conn)

    # Check for existing tenant
    existing = conn.execute(
        "SELECT id, name, plan, state, created_at FROM tenants WHERE id = ?",
        (tenant_id,),
    ).fetchone()

    if existing is not None:
        # Idempotent: return existing info without modifying secrets or inserting duplicates
        existing_entity = conn.execute(
            "SELECT id, name FROM legal_entities WHERE COALESCE(tenant_id, 'TENANT-HQ') = ?",
            (tenant_id,),
        ).fetchone()
        existing_admin = conn.execute(
            "SELECT id, name, role FROM identities WHERE COALESCE(tenant_id, 'TENANT-HQ') = ? AND role = 'ceo'",
            (tenant_id,),
        ).fetchone()
        return {
            "created": False,
            "tenant_id": existing["id"],
            "name": existing["name"],
            "state": existing["state"],
            "entity_id": existing_entity["id"] if existing_entity else None,
            "admin": dict(existing_admin) if existing_admin else None,
        }

    # Resolve default entity ID and admin name if not explicitly provided
    if not entity_id:
        suffix = tenant_id[7:] if tenant_id.startswith("TENANT-") else tenant_id
        entity_id = f"LE-{suffix}-HQ"

    if not admin_name:
        suffix = tenant_id[7:] if tenant_id.startswith("TENANT-") else tenant_id
        admin_name = f"{suffix.lower()}-admin"

    # 1. Insert tenants row
    conn.execute(
        "INSERT INTO tenants (id, name, plan, state) VALUES (?, ?, 'standard', 'active')",
        (tenant_id, name),
    )

    # 2. Insert legal_entities row (带 tenant_id，parent_entity_id 为空，governance_mode 为 group_direct)
    conn.execute(
        """INSERT INTO legal_entities
             (id, name, tenant_id, type, parent_entity_id, governance_mode, state)
           VALUES (?, ?, ?, 'subsidiary', NULL, 'group_direct', 'active')""",
        (entity_id, name, tenant_id),
    )

    # 3. Issue CEO admin identity (identities 行，role=ceo，带 tenant_id 与 legal_entity_id)
    admin_out = identity.issue(
        conn,
        name=admin_name,
        role="ceo",
        legal_entity_id=entity_id,
        tenant_id=tenant_id,
    )

    # 4. Write audit log for tenant creation traceability
    audit.write_audit(
        conn,
        legal_entity_id=entity_id,
        verb="tenant_created",
        actor="system",
        action="tenant_created",
        target=tenant_id,
        payload={
            "tenant_id": tenant_id,
            "name": name,
            "entity_id": entity_id,
            "admin_name": admin_name,
        },
        response={"state": "active", "admin_identity_id": admin_out["id"]},
    )

    conn.commit()

    return {
        "created": True,
        "tenant_id": tenant_id,
        "name": name,
        "entity_id": entity_id,
        "admin": admin_out,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Tenant provisioning tool: create tenant, entity, and CEO identity, or list tenants."
    )
    parser.add_argument("--id", help="Tenant ID (e.g. TENANT-BETA)")
    parser.add_argument("--name", help="Tenant display name (e.g. 贝塔科技)")
    parser.add_argument("--entity", default=None, help="Root legal entity ID (e.g. LE-BETA-HQ)")
    parser.add_argument("--admin-name", default=None, help="Admin identity name (e.g. beta-admin)")
    parser.add_argument("--list", action="store_true", help="List all tenants and counts of entities/identities")

    args = parser.parse_args(argv)

    init_db()
    conn = connect()
    try:
        if args.list:
            return list_tenants(conn)

        if not args.id or not args.name:
            parser.error("创建租户时必须指定 --id 与 --name（或者使用 --list 查看租户列表）")

        result = create_tenant(
            conn,
            tenant_id=args.id,
            name=args.name,
            entity_id=args.entity,
            admin_name=args.admin_name,
        )

        if not result["created"]:
            print(f"[已存在] 租户已存在: id={result['tenant_id']} (名称: {result['name']})")
            if result.get("entity_id"):
                print(f"  关联实体 ID: {result['entity_id']}")
            if result.get("admin"):
                print(f"  管理员身份 ID: {result['admin']['id']} (名称: {result['admin']['name']})")
            print("  提示: 幂等跳过，未执行重复插入，既有密钥与配置保持不变。")
            return 0

        admin = result["admin"]
        print("============================================================")
        print("租户开通成功！")
        print(f"  租户 ID:        {result['tenant_id']}")
        print(f"  租户名称:      {result['name']}")
        print(f"  实体 ID:        {result['entity_id']}")
        print(f"  管理员名称:    {admin['name']}")
        print(f"  管理员身份 ID: {admin['id']}")
        print(f"  管理员密钥:    {admin['secret']}")
        print("------------------------------------------------------------")
        print("【重要提示】")
        print("  管理员密钥 (secret) 只此一次在控制台明文输出，系统数据库仅保存")
        print("  用于 HMAC 验证的密钥。请立即将该密钥安全妥善保存，切勿丢失或泄露！")
        print("  如需配置到客户端或代理环境，可写入环境变量：")
        print(f"    OA_ID={admin['id']}")
        print(f"    OA_SECRET={admin['secret']}")
        print("============================================================")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
