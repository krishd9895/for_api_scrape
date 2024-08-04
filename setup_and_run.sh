#!/bin/bash

# Update the package list
sudo apt-get update

# Install libnss and libnspr packages
sudo apt-get install -y libnss3 libnss3-dev libnspr4 libnspr4-dev

# Install additional dependencies
sudo apt-get install -y libglib2.0-0 libgobject-2.0-0 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 libgio2.0-0 libexpat1 libatspi2.0-0 libx11-6 libxcomposite1 libxdamage1 libxext6 libxfixes3 libxrandr2 libgbm1 libdrm2 libxcb1 libxkbcommon0 libasound2 libc6 libffi7 libpcre3 libsystemd0 libgmodule-2.0-0 zlib1g libmount1 libselinux1 libres0 libxrender1 libwayland-server0 libxau6 libxdmcp6 librt0 liblzma5 liblz4-1 libgcrypt20 libblkid1 libpcre2-8-0 libbsd0 libgpg-error0

# Install Python dependencies
pip install -r requirements.txt

# Run the main Python script
python main.py
