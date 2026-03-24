"""
Task 3.1.5 Validation Script: AI customer speaks for each persona.

Tests the full Realtime WebSocket pipeline for Dana, Anne, and Victor.
Connects to the backend proxy, sends the persona session config + a text
greeting from the "agent", and verifies the AI generates audio (speech).

Run from the backend/ directory:
    python validate_voice.py
"""
import asyncio
import json
import sys
import websockets


BACKEND_WS = "ws://localhost:8000/ws/realtime"

# Mirror the JS prompts from index.html
_DANA_PROMPT = (
    "You are Dana Lakehouse, a hotel guest. You called Contoso Resorts.\n\n"
    "WHY YOU CALLED: You want to book a trip this weekend.\n\n"
    "RULES:\n"
    "- You are the customer. Wait for the agent to greet you first.\n"
    "- Keep every reply to 1-2 sentences.\n"
    "- Never break character."
)

_ANNE_PROMPT = (
    "You are Anne Thropic, a hotel guest. You called Contoso Resorts.\n\n"
    "WHY YOU CALLED: Your daughter Emma's birthday falls during your upcoming stay.\n\n"
    "RULES:\n"
    "- You are the customer. Wait for the agent to greet you first.\n"
    "- Keep every reply to 1-2 sentences.\n"
    "- Never break character."
)

_VICTOR_PROMPT = (
    "You are Victor Storr, a hotel guest. You called Contoso Resorts.\n\n"
    "WHY YOU CALLED: You need to change a booking.\n\n"
    "RULES:\n"
    "- You are the customer. Wait for the agent to greet you first.\n"
    "- Keep every reply to 1-2 sentences.\n"
    "- Never break character."
)

# Per-persona voice and scenario (defined after prompts)
PERSONAS = [
    {"id": "dana",   "name": "Dana Lakehouse", "voice": "shimmer", "prompt": _DANA_PROMPT},
    {"id": "anne",   "name": "Anne Thropic",   "voice": "coral",  "prompt": _ANNE_PROMPT},
    {"id": "victor", "name": "Victor Storr",   "voice": "echo",    "prompt": _VICTOR_PROMPT},
]

# Agent greeting that mimics saying hello (injected as text input)
AGENT_GREETING = (
    "Thank you for calling Contoso Resorts, this is Alex. "
    "How can I help you today?"
)


async def validate_persona(persona: dict) -> dict:
    """
    Connects to /ws/realtime?persona=<id>, configures the session, sends a
    text-mode greeting as the agent, and waits for the AI to produce audio.

    Returns a result dict with keys:
        - persona: persona id
        - connected: bool
        - session_created: bool
        - ai_spoke: bool  (True if response.audio_delta received)
        - transcript: str (partial AI transcript if available)
        - error: str or None
    """
    result = {
        "persona": persona["id"],
        "name": persona["name"],
        "voice": persona["voice"],
        "connected": False,
        "session_created": False,
        "ai_spoke": False,
        "transcript": "",
        "error": None,
    }

    url = f"{BACKEND_WS}?persona={persona['id']}"
    print(f"\n{'='*60}")
    print(f"Testing persona: {persona['name']} ({persona['voice']} voice)")
    print(f"  URL: {url}")

    try:
        async with websockets.connect(
            url,
            subprotocols=["realtime"],
            open_timeout=15,
            ping_timeout=None,
        ) as ws:
            result["connected"] = True
            print(f"  [OK] WebSocket connected to backend proxy")

            # Send session.update — mirror what the browser JS does
            session_cfg = {
                "type": "session.update",
                "session": {
                    "modalities": ["text", "audio"],
                    "instructions": persona["prompt"],
                    "voice": persona["voice"],
                    "input_audio_format": "pcm16",
                    "output_audio_format": "pcm16",
                    "turn_detection": None,  # manual turn for test
                    "input_audio_transcription": {"model": "whisper-1"},
                },
            }
            await ws.send(json.dumps(session_cfg))
            print(f"  [>>] Sent session.update")

            # Wait for session.created / session.updated (from Azure via proxy)
            deadline = asyncio.get_event_loop().time() + 15
            while asyncio.get_event_loop().time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5)
                    evt = json.loads(raw)
                    evt_type = evt.get("type", "")

                    if evt_type in ("session.created", "session.updated"):
                        result["session_created"] = True
                        print(f"  [OK] Received {evt_type} from Azure Realtime")
                        break
                    elif evt_type == "error":
                        result["error"] = evt.get("error", {}).get("message", str(evt))
                        print(f"  [ERR] Azure error: {result['error']}")
                        return result
                except asyncio.TimeoutError:
                    print(f"  [WAIT] Still waiting for session event...")
                    continue

            if not result["session_created"]:
                result["error"] = "Timed out waiting for session.created"
                return result

            # Send the agent greeting as a text conversation item, then request response
            agent_item = {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": AGENT_GREETING}
                    ],
                },
            }
            await ws.send(json.dumps(agent_item))
            print(f"  [>>] Injected agent greeting as text")

            response_create = {"type": "response.create"}
            await ws.send(json.dumps(response_create))
            print(f"  [>>] Sent response.create — waiting for AI to speak...")

            # Wait for audio delta or text delta (AI generating response)
            deadline = asyncio.get_event_loop().time() + 30
            audio_chunks = 0
            text_so_far = ""

            while asyncio.get_event_loop().time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=10)
                    evt = json.loads(raw)
                    evt_type = evt.get("type", "")

                    if evt_type == "response.audio.delta":
                        audio_chunks += 1
                        if audio_chunks == 1:
                            print(f"  [OK] AI is generating AUDIO — first audio chunk received!")
                        result["ai_spoke"] = True

                    elif evt_type == "response.text.delta":
                        text_so_far += evt.get("delta", "")

                    elif evt_type == "response.audio_transcript.delta":
                        result["transcript"] += evt.get("delta", "")

                    elif evt_type == "response.done":
                        print(f"  [OK] AI response complete — {audio_chunks} audio chunks")
                        if result["transcript"]:
                            print(f"  [TRANSCRIPT] {result['transcript'][:200]}")
                        if text_so_far:
                            print(f"  [TEXT] {text_so_far[:200]}")
                        break

                    elif evt_type == "error":
                        result["error"] = evt.get("error", {}).get("message", str(evt))
                        print(f"  [ERR] Runtime error: {result['error']}")
                        break

                except asyncio.TimeoutError:
                    if result["ai_spoke"]:
                        break
                    print(f"  [WAIT] Waiting for AI audio...")

    except Exception as exc:
        result["error"] = str(exc)
        print(f"  [ERR] Exception: {exc}")

    return result


async def main():
    print("\nContoso Resorts AI — Task 3.1.5 Voice Validation")
    print("="*60)
    print("Validates that the AI customer speaks for each demo persona.")
    print("Requires backend running at localhost:8000\n")

    results = []
    for persona in PERSONAS:
        r = await validate_persona(persona)
        results.append(r)
        # Brief pause between tests
        await asyncio.sleep(2)

    # Summary
    print(f"\n{'='*60}")
    print("VALIDATION SUMMARY — Task 3.1.5")
    print(f"{'='*60}")
    all_pass = True
    for r in results:
        status = "PASS" if (r["connected"] and r["session_created"] and r["ai_spoke"]) else "FAIL"
        if status == "FAIL":
            all_pass = False
        voice_tag = f"({r['voice']})" if r.get("voice") else ""
        print(f"  [{status}] {r['name']} {voice_tag}")
        if r["error"]:
            print(f"         ↳ Error: {r['error']}")
        elif r["transcript"]:
            snippet = r["transcript"][:100].replace("\n", " ")
            print(f"         ↳ Said: \"{snippet}...\"")

    print()
    if all_pass:
        print("RESULT: ALL PERSONAS PASS — Task 3.1.5 validated ✓")
        sys.exit(0)
    else:
        print("RESULT: ONE OR MORE PERSONAS FAILED")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
