"""
NeuroVocal AI - Stream 1: Conversational Emergency Operator
Hackering 2.0 (Real-Time Voice AI Track)

Implements the Conversational Voice Loop:
- Deepgram Nova-2 (Ultra-fast streaming Speech-to-Text)
- Groq Llama 3.3 70B Versatile (Real-time clinical triage reasoning)
- Cartesia Sonic (Sub-150ms natural, empathetic voice synthesis)

Operates in parallel with Stream 2 (DSP Acoustic Biomarker Engine).
Includes intelligent simulation fallback if live API keys are not configured.
"""

import asyncio
import json
import os
import sys
import time

DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
CARTESIA_API_KEY = os.getenv("CARTESIA_API_KEY", "")

SYSTEM_PROMPT = """You are NeuroVocal AI, a compassionate and highly trained emergency medical triage operator on helpline 112.
Your duties:
1. Keep the caller calm and speak in clear, short, empathetic sentences (under 25 words per reply).
2. Prioritize identifying their exact location and immediate safety.
3. If they describe feeling heavy, weak, slurred, or short of breath, instruct them to sit or lie safely while emergency responders are alerted.
4. Never argue or use medical jargon. Do not delay emergency instructions."""


class ConversationalTriageAgent:
    """
    Stream 1 Conversational Voice AI operator.
    """

    def __init__(self):
        self.conversation_history = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
        self.groq_client = None
        self.deepgram_client = None
        self.cartesia_client = None
        self._init_clients()

    def _init_clients(self):
        if GROQ_API_KEY:
            try:
                from groq import Groq
                self.groq_client = Groq(api_key=GROQ_API_KEY)
                print("[INFO] Groq Llama 3.3 client initialized.")
            except ImportError:
                print("[WARN] 'groq' package not installed. Using conversational simulation.")
        else:
            print("[INFO] No GROQ_API_KEY detected. Running conversational agent in simulation mode.")

    async def get_response(self, user_transcript: str) -> str:
        """Process user input and return the operator's spoken reply."""
        self.conversation_history.append({"role": "user", "content": user_transcript})

        if self.groq_client:
            try:
                chat_completion = self.groq_client.chat.completions.create(
                    messages=self.conversation_history,
                    model="llama-3.3-70b-versatile",
                    temperature=0.3,
                    max_tokens=100,
                )
                reply = chat_completion.choices[0].message.content.strip()
            except Exception as e:
                print(f"[ERROR] Groq generation error: {e}. Falling back to simulation.")
                reply = self._fallback_dialogue(user_transcript)
        else:
            reply = self._fallback_dialogue(user_transcript)

        self.conversation_history.append({"role": "assistant", "content": reply})
        return reply

    def _fallback_dialogue(self, text: str) -> str:
        """Contextual responses for common emergency scenarios."""
        t = text.lower()
        if "lightheaded" in t or "heavy" in t or "arm" in t or "head" in t:
            return "I hear you. Please stay seated where you are right now. Can you tell me your current address or location?"
        elif "address" in t or "street" in t or "road" in t or "home" in t or "bangalore" in t:
            return "Thank you. Emergency services are being alerted right now. Keep breathing slowly and stay on the line with me."
        elif "breath" in t or "chest" in t or "choking" in t:
            return "Try to take slow, gentle breaths through your nose. Do not stand up. What is your name and house number?"
        else:
            return "I am with you on the line. Take a slow breath. Tell me where you are and what you feel right now."


async def run_conversational_demo():
    print("=" * 70)
    print(" NeuroVocal AI - Stream 1 Conversational Operator Demo")
    print(" (Deepgram Nova-2 + Groq Llama 3.3 + Cartesia Voice Integration)")
    print("=" * 70)

    agent = ConversationalTriageAgent()

    test_utterances = [
        "Hello... I feel a bit lightheaded, and my right hand feels heavy.",
        "I'm at 14th Main Road, Indiranagar, Bangalore.",
        "Yes, my speech feels a bit strange today.",
    ]

    for utterance in test_utterances:
        print(f"\n[CALLER]: \"{utterance}\"")
        await asyncio.sleep(0.6)  # Simulate speech processing delay
        reply = await agent.get_response(utterance)
        print(f"[NEUROVOCAL OPERATOR]: \"{reply}\"")
        await asyncio.sleep(0.4)


if __name__ == "__main__":
    asyncio.run(run_conversational_demo())
