import openai
import os
from typing import Optional


class OpenAIClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: str = "gpt-5.2-2025-12-11"
    ):
        """
        Initialize OpenAI client.

        Args:
            api_key: OpenAI API key. If not provided, will try OPENAI_API_KEY env var.
            base_url: Base URL for API (default: OpenAI official; set for Azure/custom endpoints).
            model: Model to use (default: gpt-5.2-2025-12-11)
        """
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError(
                "API key must be provided or set as OPENAI_API_KEY environment variable"
            )

        self.base_url = base_url  # None uses OpenAI default
        self.model = model

        client_kwargs = {"api_key": self.api_key}
        if self.base_url is not None:
            client_kwargs["base_url"] = self.base_url

        self.client = openai.Client(**client_kwargs)

    def chat(
        self,
        prompt: str,
        model: Optional[str] = None,
        max_tokens: int = 512,
        temperature: float = 0.7,
        timeout: float = 120.0,
        **kwargs
    ):
        """
        Make a chat completion request to OpenAI.

        Args:
            prompt: The prompt to send to the model
            model: Model to use (overrides default if provided)
            max_tokens: Maximum tokens in response
            temperature: Sampling temperature
            timeout: Request timeout in seconds (default: 120)
            **kwargs: Additional parameters to pass to the API

        Returns:
            The response content from the model
        """
        model = model or self.model
        messages = [{"content": prompt, "role": "user"}]
        create_kwargs = dict(
            model=model,
            messages=messages,
            temperature=temperature,
            timeout=timeout,
            **kwargs
        )
        # GPT-5.2+ require max_completion_tokens; send via extra_body (SDK doesn't accept it as direct arg)
        create_kwargs["extra_body"] = {**(create_kwargs.get("extra_body") or {}), "max_completion_tokens": max_tokens}

        try:
            result = self.client.chat.completions.create(**create_kwargs)
            return result.choices[0].message.content
        except Exception as e:
            raise Exception(f"Error making OpenAI API call: {str(e)}")

    def chat_with_system(
        self,
        system_prompt: str,
        user_prompt: str,
        model: Optional[str] = None,
        max_tokens: int = 512,
        temperature: float = 0.7,
        **kwargs
    ):
        """
        Make a chat completion request with a system message.

        Args:
            system_prompt: System message/prompt
            user_prompt: User message/prompt
            model: Model to use (overrides default if provided)
            max_tokens: Maximum tokens in response
            temperature: Sampling temperature
            **kwargs: Additional parameters to pass to the API

        Returns:
            The response content from the model
        """
        model = model or self.model
        messages = [
            {"content": system_prompt, "role": "system"},
            {"content": user_prompt, "role": "user"},
        ]
        create_kwargs = dict(
            model=model,
            messages=messages,
            temperature=temperature,
            **kwargs
        )
        create_kwargs["extra_body"] = {**(create_kwargs.get("extra_body") or {}), "max_completion_tokens": max_tokens}

        try:
            result = self.client.chat.completions.create(**create_kwargs)
            return result.choices[0].message.content
        except Exception as e:
            raise Exception(f"Error making OpenAI API call: {str(e)}")


def main():
    """Example usage"""
    import sys

    # You can set the API key as an environment variable or pass it directly
    # export OPENAI_API_KEY="your-api-key-here"

    if len(sys.argv) < 2:
        print("Usage: python openai_client.py <prompt> [api_key]")
        print("Or set OPENAI_API_KEY environment variable")
        sys.exit(1)

    prompt = sys.argv[1]
    api_key = sys.argv[2] if len(sys.argv) > 2 else None

    try:
        client = OpenAIClient(api_key=api_key)
        response = client.chat(prompt)
        print(response)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
