import asyncio

from .AzureAISearch import BookSearchClient


def print_hit(hit: dict, idx: int):
    print("=" * 80)
    print(f"Hit #{idx + 1}  score={hit.get('score')}")
    print("id:        ", hit.get("id"))
    print("title:     ", hit.get("title"))
    print("chapter:   ", hit.get("chapter_title"))
    print("authors:   ", hit.get("book_authors"))
    print("subjects:  ", hit.get("book_subjects"))
    print("- text preview -")
    text = (hit.get("text") or "").replace("\n", " ")
    print(text[:400] + ("..." if len(text) > 400 else ""))
    print()


async def main():
    client = BookSearchClient()

    query = "Theodore Roosevelt conservation speech"

    print(f"Running book search for query: {query!r}")
    hits = await client.search(query, top_k=5)

    print(f"Got {len(hits)} hits")
    for i, h in enumerate(hits):
        print_hit(h, i)

    if hits:
        h0 = hits[0]
        assert "id" in h0
        assert "text" in h0
        assert "title" in h0
        assert "description" in h0
        print("\nBasic field assertions passed")


if __name__ == "__main__":
    asyncio.run(main())
