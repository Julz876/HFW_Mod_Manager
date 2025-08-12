import os
import shutil
import subprocess
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def _run_and_copy_core_stream(
    cmd: list[str],
    work_dir: str,
    pack_subdir: str
) -> list[str]:
    """
    Internal helper:  
      1) ensures work_dir exists,  
      2) runs `cmd` in work_dir,  
      3) logs whatever the packer prints to stdout/stderr,  
      4) looks for any *.core or *.stream files in work_dir, and copies them into
         pack_dir (work_dir/pack_subdir), overwriting if necessary.  
      Never deletes any existing files.  

    Returns a list of filenames that were copied into pack_dir.  
    """
    os.makedirs(work_dir, exist_ok=True)
    pack_dir = os.path.join(work_dir, pack_subdir)
    os.makedirs(pack_dir, exist_ok=True)

    logging.info("Running packer: %s", ' '.join(cmd))
    result = subprocess.run(cmd, cwd=work_dir, capture_output=True, text=True)

    if result.stdout:
        logging.info("Packer stdout:\n%s", result.stdout)
    if result.stderr:
        logging.error("Packer stderr:\n%s", result.stderr)

    # If returned nonzero code, raise an exception
    result.check_returncode()

    # Copy
    copied_files = []
    for fname in os.listdir(work_dir):
        lower = fname.lower()
        if lower.endswith('.core') or lower.endswith('.stream'):
            src = os.path.join(work_dir, fname)
            dst = os.path.join(pack_dir, fname)
            try:
                shutil.copy2(src, dst)
                copied_files.append(fname)
                logging.info("Copied to pack: %s → %s", fname, os.path.join(pack_subdir, fname))
            except Exception as e:
                logging.error("Failed to copy %s: %s", fname, e)

    return copied_files


def run_packing_mesh(
    tool,
    group_id,
    lod: int,
    original_skeleton,
    new_mesh,
    mesh_id: int,
    # submesh_number: int,
    work_dir: str = ".",
    pack_subdir: str = "pack",
) -> list[str]:

    cmd = [
        tool,
        group_id,
        str(lod),
        original_skeleton,
        new_mesh,
        str(mesh_id),
        # str(submesh_number),
    ]
    return _run_and_copy_core_stream(cmd, work_dir, pack_subdir)


def run_packing_texture(
    tool: str,
    group_id: str,
    lod: int,
    new_texture: str,
    texture_number: int,
    work_dir: str = ".",
    pack_subdir: str = "pack",
) -> list[str]:

    cmd = [
        tool,
        group_id,
        str(lod),
        new_texture,
        str(texture_number),
    ]
    return _run_and_copy_core_stream(cmd, work_dir, pack_subdir)
