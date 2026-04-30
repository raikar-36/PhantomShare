"""
Multi-file bundler for PhantomShare.

Bundles multiple files/folders into a single ZIP archive for transfer,
and extracts them on the receiving end.
"""
import os
import zipfile
import tempfile
import logging
from pathlib import Path
from typing import List, Optional, Callable
from datetime import datetime

log = logging.getLogger(__name__)

# Magic header to identify PhantomShare bundles
BUNDLE_MAGIC = b"PHANTOMSHARE_BUNDLE_V1"
BUNDLE_EXTENSION = ".phantombundle.zip"


def is_bundle(filepath: Path) -> bool:
    """Check if a file is a PhantomShare bundle."""
    if not filepath.exists():
        return False
    if not filepath.name.endswith(BUNDLE_EXTENSION):
        return False
    try:
        with zipfile.ZipFile(filepath, 'r') as zf:
            if '.phantomshare_manifest' in zf.namelist():
                return True
    except Exception:
        pass
    return False


def create_bundle(
    paths: List[Path],
    output_dir: Optional[Path] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> Path:
    """
    Create a PhantomShare bundle from multiple files/folders.
    
    Args:
        paths: List of file/folder paths to bundle
        output_dir: Directory to create bundle in (default: temp dir)
        on_progress: Callback(current_file, total_files)
    
    Returns:
        Path to the created bundle file
    """
    if not paths:
        raise ValueError("No files to bundle")
    
    # Determine output path
    if output_dir is None:
        output_dir = Path(tempfile.gettempdir())
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Generate bundle name
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if len(paths) == 1:
        base_name = paths[0].stem
    else:
        base_name = f"bundle_{len(paths)}_files"
    bundle_path = output_dir / f"{base_name}_{timestamp}{BUNDLE_EXTENSION}"
    
    # Collect all files to bundle
    all_files: List[tuple[Path, str]] = []  # (absolute_path, archive_name)
    
    for path in paths:
        path = Path(path).resolve()
        if path.is_file():
            all_files.append((path, path.name))
        elif path.is_dir():
            for root, dirs, files in os.walk(path):
                root_path = Path(root)
                for fname in files:
                    file_path = root_path / fname
                    # Archive name includes folder structure
                    arc_name = str(file_path.relative_to(path.parent))
                    all_files.append((file_path, arc_name))
    
    if not all_files:
        raise ValueError("No files found to bundle")
    
    # Create ZIP archive
    total = len(all_files)
    with zipfile.ZipFile(bundle_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        # Write manifest
        manifest_content = f"PhantomShare Bundle\nFiles: {total}\nCreated: {datetime.now().isoformat()}\n"
        zf.writestr('.phantomshare_manifest', manifest_content)
        
        # Add files
        for i, (file_path, arc_name) in enumerate(all_files):
            if on_progress:
                on_progress(i + 1, total)
            try:
                zf.write(file_path, arc_name)
            except Exception as e:
                log.warning(f"Could not add {file_path}: {e}")
    
    log.info(f"Created bundle: {bundle_path} ({total} files)")
    return bundle_path


def extract_bundle(
    bundle_path: Path,
    output_dir: Path,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> List[Path]:
    """
    Extract a PhantomShare bundle.
    
    Args:
        bundle_path: Path to the bundle file
        output_dir: Directory to extract to
        on_progress: Callback(current_file, total_files)
    
    Returns:
        List of extracted file paths
    """
    bundle_path = Path(bundle_path)
    output_dir = Path(output_dir)
    
    if not is_bundle(bundle_path):
        raise ValueError("Not a valid PhantomShare bundle")
    
    output_dir.mkdir(parents=True, exist_ok=True)
    extracted: List[Path] = []
    
    with zipfile.ZipFile(bundle_path, 'r') as zf:
        members = [m for m in zf.namelist() if not m.startswith('.phantomshare')]
        total = len(members)
        
        for i, member in enumerate(members):
            if on_progress:
                on_progress(i + 1, total)
            
            # Security: prevent path traversal
            member_path = Path(member)
            if member_path.is_absolute() or '..' in member_path.parts:
                log.warning(f"Skipping unsafe path: {member}")
                continue
            
            target = output_dir / member
            target.parent.mkdir(parents=True, exist_ok=True)
            
            try:
                with zf.open(member) as src, open(target, 'wb') as dst:
                    dst.write(src.read())
                extracted.append(target)
            except Exception as e:
                log.warning(f"Could not extract {member}: {e}")
    
    log.info(f"Extracted {len(extracted)} files to {output_dir}")
    return extracted


def get_bundle_info(bundle_path: Path) -> dict:
    """Get information about a bundle without extracting."""
    bundle_path = Path(bundle_path)
    
    if not is_bundle(bundle_path):
        raise ValueError("Not a valid PhantomShare bundle")
    
    with zipfile.ZipFile(bundle_path, 'r') as zf:
        members = [m for m in zf.namelist() if not m.startswith('.phantomshare')]
        total_size = sum(zf.getinfo(m).file_size for m in members)
        
        return {
            'file_count': len(members),
            'total_size': total_size,
            'files': members[:50],  # First 50 files
            'bundle_size': bundle_path.stat().st_size,
        }
