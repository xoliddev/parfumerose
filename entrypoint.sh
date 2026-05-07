#!/bin/bash
set -e

# Data migration running
echo "🔄 Database migration (SQLite -> PostgreSQL) boshlanmoqda..."
python migrate_data.py

# Start application
echo "🚀 Application ishga tushmoqda..."
exec uvicorn web.main:app --host 0.0.0.0 --port 10000
