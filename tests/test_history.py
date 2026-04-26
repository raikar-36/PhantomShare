"""
Unit tests for PhantomShare transfer history.
"""
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest

# Patch the history module's paths before importing
@pytest.fixture(autouse=True)
def temp_history_dir(tmp_path):
    """Use a temporary directory for history database."""
    import app.history as history
    original_dir = history.HISTORY_DIR
    original_db = history.HISTORY_DB
    
    history.HISTORY_DIR = tmp_path
    history.HISTORY_DB = tmp_path / "history.db"
    
    yield tmp_path
    
    history.HISTORY_DIR = original_dir
    history.HISTORY_DB = original_db


class TestHistoryBasics:
    """Tests for basic history operations."""
    
    def test_add_transfer_creates_record(self):
        """add_transfer should create a record and return an ID."""
        from app.history import add_transfer, get_recent_transfers
        
        record_id = add_transfer(
            filename="test.txt",
            size=1024,
            direction="sent",
            status="completed",
        )
        
        assert isinstance(record_id, int)
        assert record_id > 0
        
        transfers = get_recent_transfers(limit=1)
        assert len(transfers) == 1
        assert transfers[0]["filename"] == "test.txt"
        assert transfers[0]["size"] == 1024
        assert transfers[0]["direction"] == "sent"
    
    def test_add_transfer_with_all_fields(self):
        """add_transfer should store all optional fields."""
        from app.history import add_transfer, get_recent_transfers
        
        add_transfer(
            filename="document.pdf",
            size=2048,
            direction="received",
            status="in_progress",
            sha256="abc123def456",
            session_code="test-1234",
            peer_emoji="🔐🔑🔒",
        )
        
        transfers = get_recent_transfers(limit=1)
        assert transfers[0]["sha256"] == "abc123def456"
        assert transfers[0]["session_code"] == "test-1234"
        assert transfers[0]["peer_emoji"] == "🔐🔑🔒"
    
    def test_update_transfer_status(self):
        """update_transfer_status should update the record."""
        from app.history import add_transfer, update_transfer_status, get_recent_transfers
        
        record_id = add_transfer(
            filename="updating.bin",
            size=512,
            direction="sent",
            status="in_progress",
        )
        
        update_transfer_status(record_id, "completed", sha256="final_hash")
        
        transfers = get_recent_transfers(limit=1)
        assert transfers[0]["status"] == "completed"
        assert transfers[0]["sha256"] == "final_hash"
    
    def test_get_recent_transfers_respects_limit(self):
        """get_recent_transfers should respect the limit parameter."""
        from app.history import add_transfer, get_recent_transfers
        
        for i in range(10):
            add_transfer(
                filename=f"file{i}.txt",
                size=100 * i,
                direction="sent",
                status="completed",
            )
        
        transfers = get_recent_transfers(limit=5)
        assert len(transfers) == 5
    
    def test_get_recent_transfers_ordered_by_timestamp(self):
        """get_recent_transfers should return most recent first."""
        from app.history import add_transfer, get_recent_transfers
        import time
        
        add_transfer(filename="first.txt", size=100, direction="sent", status="completed")
        time.sleep(0.01)  # Ensure different timestamps
        add_transfer(filename="second.txt", size=200, direction="sent", status="completed")
        time.sleep(0.01)
        add_transfer(filename="third.txt", size=300, direction="sent", status="completed")
        
        transfers = get_recent_transfers(limit=3)
        # Most recent should be first
        assert transfers[0]["filename"] == "third.txt"
        assert transfers[1]["filename"] == "second.txt"
        assert transfers[2]["filename"] == "first.txt"


class TestHistoryCleanup:
    """Tests for history cleanup behavior."""
    
    def test_clear_history_removes_all_records(self):
        """clear_history should remove all transfer records."""
        from app.history import add_transfer, clear_history, get_recent_transfers
        
        for i in range(5):
            add_transfer(filename=f"file{i}.txt", size=100, direction="sent", status="completed")
        
        clear_history()
        
        transfers = get_recent_transfers(limit=100)
        assert len(transfers) == 0
    
    def test_auto_cleanup_old_entries(self):
        """Adding entries should auto-cleanup entries beyond MAX_HISTORY_ENTRIES."""
        from app.history import add_transfer, get_recent_transfers
        import app.history as history
        
        original_max = history.MAX_HISTORY_ENTRIES
        history.MAX_HISTORY_ENTRIES = 5
        
        try:
            # Add more than max entries
            for i in range(10):
                add_transfer(
                    filename=f"file{i:02d}.txt",
                    size=100,
                    direction="sent",
                    status="completed",
                )
            
            transfers = get_recent_transfers(limit=100)
            assert len(transfers) <= 5
        finally:
            history.MAX_HISTORY_ENTRIES = original_max


class TestHistoryStats:
    """Tests for history statistics."""
    
    def test_get_stats_empty_database(self):
        """get_stats should return zeros for empty database."""
        from app.history import get_stats
        
        stats = get_stats()
        assert stats["total"] == 0
        assert stats["sent"] == 0
        assert stats["received"] == 0
        assert stats["total_bytes"] == 0
    
    def test_get_stats_with_transfers(self):
        """get_stats should count transfers correctly."""
        from app.history import add_transfer, get_stats
        
        add_transfer(filename="sent1.txt", size=1000, direction="sent", status="completed")
        add_transfer(filename="sent2.txt", size=2000, direction="sent", status="completed")
        add_transfer(filename="recv1.txt", size=3000, direction="received", status="completed")
        add_transfer(filename="failed.txt", size=5000, direction="sent", status="failed")
        
        stats = get_stats()
        assert stats["total"] == 4
        assert stats["sent"] == 3
        assert stats["received"] == 1
        # total_bytes only counts completed transfers
        assert stats["total_bytes"] == 6000  # 1000 + 2000 + 3000


class TestHistoryPersistence:
    """Tests for database persistence."""
    
    def test_database_persists_across_calls(self, temp_history_dir):
        """Database should persist data across function calls."""
        from app.history import add_transfer, get_recent_transfers, init_db
        
        add_transfer(filename="persistent.txt", size=999, direction="sent", status="completed")
        
        # Verify the database file exists
        db_path = temp_history_dir / "history.db"
        assert db_path.exists()
        
        # Re-init and verify data persists
        transfers = get_recent_transfers(limit=1)
        assert len(transfers) == 1
        assert transfers[0]["filename"] == "persistent.txt"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
