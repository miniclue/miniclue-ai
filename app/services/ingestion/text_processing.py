import tiktoken


def chunk_text_by_tokens(
    text: str,
    chunk_size: int = 1000,
    overlap: int = 200,
) -> list[tuple[str, int]]:
    encoder = tiktoken.get_encoding("cl100k_base")
    all_tokens = encoder.encode(text)
    chunks: list[tuple[str, int]] = []
    step = chunk_size - overlap

    for i in range(0, len(all_tokens), step):
        # slice out the next chunk of token IDs
        chunk_tokens = all_tokens[i : i + chunk_size]
        # decode tokens back to text
        chunk_text_value = encoder.decode(chunk_tokens)
        token_count = len(chunk_tokens)
        chunks.append((chunk_text_value, token_count))
        if i + chunk_size >= len(all_tokens):
            break

    return chunks
