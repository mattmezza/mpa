# Voice Interaction

## Receiving voice messages

Voice messages are automatically transcribed using Whisper before being passed to you.
You see the transcript as regular text, with a `[voice]` prefix.

## Sending voice responses

Add `[respond_with_voice]` at the end of your response to trigger TTS.

Use voice responses when:
- The user sent a voice message (mirror the medium).
- The user explicitly asks for a voice reply.
- The response is short and conversational (< 3 sentences).

Do NOT use voice responses when:
- The response contains code, links, or structured data.
- The response is long or complex.
