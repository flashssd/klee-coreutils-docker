import openai
import os
from typing import Optional


class OpenRouterClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://openrouter.ai/api/v1",
        model: str = "qwen/qwen-2.5-72b-instruct"
    ):
        """
        Initialize OpenRouter client.
        
        Args:
            api_key: OpenRouter API key. If not provided, will try to get from OPENROUTER_API_KEY or OPENAI_API_KEY env var.
            base_url: Base URL for OpenRouter API (default: https://openrouter.ai/api/v1)
            model: Model to use (default: qwen/qwen-2.5-72b-instruct)
        """
        # Check for OPENROUTER_API_KEY first, then fall back to OPENAI_API_KEY (OpenRouter is OpenAI-compatible)
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("API key must be provided or set as OPENROUTER_API_KEY or OPENAI_API_KEY environment variable")
        
        self.base_url = base_url
        self.model = model
        
        self.client = openai.Client(
            api_key=self.api_key,
            base_url=self.base_url
        )
    
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
        Make a chat completion request to OpenRouter.
        
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
        
        try:
            result = self.client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "content": prompt,
                        "role": "user",
                    }
                ],
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=timeout,
                **kwargs
            )
            return result.choices[0].message.content
        except Exception as e:
            raise Exception(f"Error making OpenRouter API call: {str(e)}")
    
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
        
        try:
            result = self.client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "content": system_prompt,
                        "role": "system",
                    },
                    {
                        "content": user_prompt,
                        "role": "user",
                    }
                ],
                max_tokens=max_tokens,
                temperature=temperature,
                **kwargs
            )
            return result.choices[0].message.content
        except Exception as e:
            raise Exception(f"Error making OpenRouter API call: {str(e)}")


def main():
    """Example usage"""
    import sys
    
    # You can set the API key as an environment variable or pass it directly
    # export OPENROUTER_API_KEY="your-api-key-here"
    
    if len(sys.argv) < 2:
        print("Usage: python openrouter_client.py <prompt> [api_key]")
        print("Or set OPENROUTER_API_KEY environment variable")
        sys.exit(1)
    
    prompt = sys.argv[1]
    api_key = sys.argv[2] if len(sys.argv) > 2 else None
    
    try:
        client = OpenRouterClient(api_key=api_key)
        response = client.chat(prompt)
        print(response)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
