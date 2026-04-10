from setuptools import setup, find_packages

setup(
    name="smartscraper",
    version="2.0.0",
    description="Deal Finder & Auto-Learning Web Scraper — Amazon & eBay deals sorted by discount",
    author="Matthew Hardy",
    packages=find_packages(),
    python_requires=">=3.8",
    install_requires=[
        "requests",
        "beautifulsoup4",
        "lxml",
        "flask",
        "playwright",
    ],
    entry_points={
        "console_scripts": [
            "smartscraper=cli:main",
        ],
    },
)
