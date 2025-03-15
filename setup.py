from setuptools import setup, find_packages

# Centralize version number
VERSION = "1.0.0"

setup(
    name="trader-magic",
    version=VERSION,
    packages=find_packages(),
    install_requires=[
        # Requirements are already in requirements.txt
    ],
)