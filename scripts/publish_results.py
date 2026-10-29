"""Publish completed, preliminary batch results; run from any working directory."""
import csv
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / 'results'

def git(*args):
    return subprocess.run(['git', *args], cwd=ROOT, check=True, text=True, capture_output=True).stdout.strip()

def atomic_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(text)
    temporary.replace(path)

def main():
    manifest = list(csv.DictReader((RESULTS / 'input_manifest.csv').open()))
    rows, provenance = [], {}
    for item in manifest:
        sample = item['sample_id']
        source = ROOT / 'data/output' / sample
        summary = source / 'image_prediction.csv'
        overlay = source / 'kernels_outlined_red_FDK_green_healthy.png'
        evidence = source / 'provenance.json'
        # The pipeline writes the summary last, after the overlay and provenance.
        if not all(p.is_file() for p in (summary, overlay, evidence)):
            continue
        record = list(csv.DictReader(summary.open()))[0]
        if record['sample_id'] != sample:
            raise ValueError(f'Sample mismatch: {sample}')
        destination = RESULTS / 'outlined' / f'{sample}.png'
        destination.parent.mkdir(exist_ok=True)
        if not destination.exists() or destination.stat().st_size != overlay.stat().st_size or destination.stat().st_mtime_ns != overlay.stat().st_mtime_ns:
            temporary = destination.with_suffix('.png.tmp')
            shutil.copy2(overlay, temporary)
            temporary.replace(destination)
        rows.append({
            **item,
            'detected_kernel_count': int(record['detected_objects']),
            'automated_fdk_count': int(record['fdk_calls']),
            'fdk_percentage': float(record['fdk_percent_all_retained_objects']),
            'non_wheat_review_flags': record['non_wheat_review_flags'],
            'outlined_image': f'outlined/{sample}.png',
            'review_status': 'preliminary_automated_unreviewed',
        })
        evidence_data = json.loads(evidence.read_text())
        provenance[sample] = {key: evidence_data[key] for key in ('source_sha256', 'models_sha256', 'method', 'scope', 'denominator')}
    if not rows:
        print('No completed results available.')
        return
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=rows[0].keys(), lineterminator='\n')
    writer.writeheader(); writer.writerows(rows)
    atomic_text(RESULTS / 'image_scores.csv', stream.getvalue())
    atomic_text(RESULTS / 'provenance.json', json.dumps(provenance, indent=2, sort_keys=True) + '\n')
    readme = '''# Preliminary automated FDK results

These are automated predictions, not verified FDK measurements. The detected kernel count is the number of retained detections; fragments, duplicates, or non-wheat objects may be included. FDK percentage = 100 × automated FDK calls / retained detections. Non-wheat flags do not remove detections from this denominator.

A seven-image visual spot check across Illinois, Nebraska and SDSU found recurring partial masks and some missed kernels. Mask errors may affect both counts and classification features. Expert FDK labels were not available for that review. Do not interpret these results as independently validated measurements.

Full outlined PNGs use Git LFS. Red means predicted FDK; green means predicted healthy. Colors are predictions, not reference labels. Run `git lfs pull` after cloning to download the images.

`image_scores.csv` contains the numerical results. `input_manifest.csv` defines the full scoring batch. `provenance.json` records source-image and model hashes. Missing rows are unfinished or failed jobs, not zero scores. Local intermediate files and mask archives are excluded.

'''
    readme += f'Completed results currently published: **{len(rows)} / {len(manifest)}**.\n\n'
    readme += '| Sample | Group | Detected count | Predicted FDK (%) | Outlined image |\n|---|---|---:|---:|---|\n'
    for row in rows:
        readme += f"| {row['sample_id']} | {row['site']} | {row['detected_kernel_count']} | {row['fdk_percentage']:.2f} | [View]({row['outlined_image']}) |\n"
    atomic_text(RESULTS / 'README.md', readme)
    paths = ['results', 'scripts/publish_results.py', '.gitattributes', 'README.md']
    git('add', '--', *paths)
    changed = git('diff', '--cached', '--name-only', '--', *paths)
    if changed:
        git('commit', '--only', '-m', f'Publish preliminary automated results ({len(rows)}/{len(manifest)} images)', '--', *paths)
    # Also retries a previously committed upload if an earlier push failed.
    push = subprocess.run(['git', 'push'], cwd=ROOT, text=True)
    push.check_returncode()
    print(f'Published {len(rows)}/{len(manifest)} completed results.')

if __name__ == '__main__':
    main()
