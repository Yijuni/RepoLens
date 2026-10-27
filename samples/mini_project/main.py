"""Small, intentionally non-sensitive repository for the local preview."""

from service import build_report


def main():
    return build_report(["read code", "follow imports", "understand modules"])


if __name__ == "__main__":
    print(main())
