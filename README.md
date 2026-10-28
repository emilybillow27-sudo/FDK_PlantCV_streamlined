# FDK phenotyping pipeline

Research software for image-based phenotyping of Fusarium-damaged kernels (FDK) in wheat. The pipeline processes images of spaced wheat kernels on a white background, detects individual kernels, applies segmentation quality control, and classifies retained detections for FDK scoring.

## Project status

This repository contains the pipeline source code. Research images, annotations, trained model weights, and generated results are excluded. A fresh clone requires the model files listed below before scoring can run. Model availability and redistribution terms have not yet been established.

This is research software under development. Performance should be evaluated for the intended imaging setup and sample population before use.

## Setup

A separate environment is recommended so the pipeline dependencies do not conflict with other projects. The current working environment uses **Python 3.11.16**. Exact direct dependency versions are listed in [`requirements.txt`](requirements.txt).

### Recommended: Conda (including VS Code)

Install Conda, clone or download this repository, and open its folder in VS Code. In the integrated terminal, run:

```bash
conda env create -f environment.yml
conda activate fdk-phenotyping
```

In VS Code, open the Command Palette, choose **Python: Select Interpreter**, and select `fdk-phenotyping`. Activate the environment in each new terminal before running the pipeline.

### Alternative: Python venv

With Python 3.11 installed:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

On Windows, activate with `.venv\Scripts\Activate.ps1` in PowerShell instead of `source`. Interactive review commands require Tkinter; the Conda environment includes Tk. A venv uses the Tk support provided by its Python installation.

The dependencies include NumPy, Pillow, SciPy, scikit-image, scikit-learn, Joblib, PyTorch, Torchvision, and Ultralytics. **scikit-learn 1.9.1 matches the saved model bundles**; other versions can produce model-loading warnings or incompatible results. The pinned versions were read from the local working macOS environment, where an image was successfully scored. A fresh installation and Windows/Linux compatibility have not yet been verified. PyTorch uses Apple MPS when available and otherwise CPU; this script does not select CUDA.

An environment provides the software dependencies; scoring also requires the trained models below. Without them, a fresh clone cannot score images.

Place these trusted model files in `data/models/`:

- `best.pt` — kernel detection/segmentation weights
- `segmentation_qc_v2.joblib` — segmentation quality-control model
- `fdk_classifier_468_labels.joblib` — FDK classifier
- `weed_gate_sdsu_candidate.joblib` — optional non-wheat review gate

Only load model files from a trusted source; serialized models can execute code when loaded.

## Usage

Check the required model bundles:

```bash
python fdk_pipeline.py check
```

Score one image:

```bash
python fdk_pipeline.py score --image path/to/image.jpg --sample-id sample_001
```

Score a folder:

```bash
python fdk_pipeline.py score-folder --images-dir path/to/images --site SITE
```

View all commands and command-specific options:

```bash
python fdk_pipeline.py --help
python fdk_pipeline.py score --help
```

Results are written under `data/output/`. Images are EXIF-oriented, converted to RGB, and cropped by 5% on each edge before processing. Red/green outlines visualize detected masks. Automatic scores include every retained detection; non-wheat flags are review cues only.

The script also includes manual review, candidate classifier training, and comparison workflows. Candidate development results should not be interpreted as independent validation.

## Repository contents

- `fdk_pipeline.py`: command-line pipeline and interactive review tools
- `requirements.txt`: direct Python dependencies
- `.gitignore`: exclusions for local research assets and generated files

## Licensing

No license has been selected yet. Contact the repository owner about reuse or collaboration.
