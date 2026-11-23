"""ArchWise cloud pricing ETL (PRD §7).

extract -> raw immutable snapshots -> normalize -> validate -> atomic publish to PostgreSQL.
Run with:  python -m pricing_etl --help
"""
