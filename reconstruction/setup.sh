#!/bin/bash
# Setup script for RealEstAI Reconstruction Engine

echo "Setting up RealEstAI Reconstruction Engine..."

# Check Python version
python_version=$(python3 --version 2>&1 | awk '{print $2}')
echo "Python version: $python_version"

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Install base requirements
echo "Installing base requirements..."
pip install -r requirements.txt

# Check for GPU
echo "Checking for GPU..."
python3 -c "import torch; print('CUDA available:', torch.cuda.is_available())" 2>/dev/null || echo "PyTorch not installed yet"

# Check for COLMAP
echo "Checking for COLMAP..."
if command -v colmap &> /dev/null; then
    echo "COLMAP is installed: $(colmap --version)"
else
    echo "COLMAP not found"
    echo "Install COLMAP:"
    echo "  Mac: brew install colmap"
    echo "  Linux: sudo apt-get install colmap"
    echo "  Conda: conda install -c conda-forge colmap"
fi

# Ask about PyTorch installation
echo ""
echo "For Gaussian Splatting training, you need PyTorch with CUDA."
echo "This requires an NVIDIA GPU with CUDA 11.8+"
echo ""
read -p "Install PyTorch with CUDA now? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "Installing PyTorch with CUDA 11.8..."
    pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118
    
    echo "Installing nerfstudio..."
    pip install nerfstudio
else
    echo "Skipping PyTorch/nerfstudio installation."
    echo "You can install them later with:"
    echo "  pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118"
    echo "  pip install nerfstudio"
fi

echo ""
echo "Setup complete!"
echo "Run the service with: python main.py"
echo "Or run tests with: pytest tests/"
