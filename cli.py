"""SmartScraper CLI - Scrape any website from the command line."""
import argparse
import json
import sys
from smartscraper import SmartScraper


def main():
    parser = argparse.ArgumentParser(description="SmartScraper - Auto-learn web scraping rules")
    sub = parser.add_subparsers(dest="command", required=True)

    # Build command
    build = sub.add_parser("build", help="Learn rules from a URL and sample data")
    build.add_argument("url", help="Target URL")
    build.add_argument("-w", "--wanted", nargs="+", required=True, help="Sample data to match")
    build.add_argument("-s", "--save", help="Save model to file")
    build.add_argument("-o", "--output", choices=["json", "text"], default="text")

    # Scrape command
    scrape = sub.add_parser("scrape", help="Scrape a URL using saved rules")
    scrape.add_argument("url", help="Target URL")
    scrape.add_argument("-m", "--model", required=True, help="Model file to load")
    scrape.add_argument("-e", "--exact", action="store_true", help="Use exact mode")
    scrape.add_argument("-o", "--output", choices=["json", "text"], default="text")

    # Rules command
    rules = sub.add_parser("rules", help="View rules in a saved model")
    rules.add_argument("model", help="Model file")

    args = parser.parse_args()
    scraper = SmartScraper()

    if args.command == "build":
        results = scraper.build(url=args.url, wanted_list=args.wanted)
        if args.save:
            scraper.save(args.save)
            print(f"Model saved to {args.save}.json", file=sys.stderr)
        if args.output == "json":
            print(json.dumps(results, indent=2))
        else:
            for r in results:
                print(r)

    elif args.command == "scrape":
        scraper.load(args.model)
        if args.exact:
            results = scraper.get_result_exact(url=args.url)
        else:
            results = scraper.get_result_similar(url=args.url)
        if args.output == "json":
            print(json.dumps(results, indent=2))
        else:
            for r in results:
                print(r)

    elif args.command == "rules":
        scraper.load(args.model)
        print(json.dumps(scraper.get_rules(), indent=2))


if __name__ == "__main__":
    main()
