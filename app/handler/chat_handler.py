"""Handles text chat via Azure OpenAI with Azure Search RAG.

This handler uses the SAME Azure Search index (insurance-index) that the
voice agent uses, ensuring consistent knowledge base access.
"""

import logging
from openai import AsyncAzureOpenAI

logger = logging.getLogger(__name__)

# System prompt matching the voice agent personality
SYSTEM_PROMPT = """You are Sarah, a friendly and professional insurance agent for Protective Life Insurance Company.
Your role is to assist applicants completing their life insurance applications.

Guidelines:
- Be helpful, patient, and clear in your explanations
- Help applicants understand insurance terminology
- Guide them through the application process
- Answer questions about coverage options, premiums, and policy details
- Use information from the knowledge base when answering questions about policies
- If asked about specific medical conditions, provide general guidance but note that underwriting will review their full application
- Be conversational but professional
- Keep responses concise and focused (2-3 sentences when possible)
- If you don't know something specific about their policy, suggest they speak with a human agent

You can help with:
- Explaining coverage types (term life, whole life, etc.)
- Clarifying application questions
- Discussing beneficiary options
- General questions about the insurance process
- Guiding them to switch to voice mode for complex discussions
- Escalating to a human agent when needed

Remember: You're helping real people protect their families. Be empathetic and supportive."""


class ChatHandler:
    """Handles text-based chat using Azure OpenAI with Azure Search RAG."""

    def __init__(self, config):
        """Initialize the chat handler with Azure OpenAI and Search configuration.

        Args:
            config: Application configuration containing Azure credentials
        """
        self.endpoint = config.get('AZURE_VOICE_LIVE_ENDPOINT', '').rstrip('/')
        self.api_key = config.get('AZURE_VOICE_LIVE_API_KEY', '')
        self.model = config.get('MODEL_DEPLOYMENT_NAME', 'gpt-4o')

        # Azure Search configuration for RAG
        self.search_endpoint = config.get('AZURE_SEARCH_ENDPOINT', '')
        self.search_index = config.get('AZURE_SEARCH_INDEX_NAME', '')
        self.search_key = config.get('AZURE_SEARCH_API_KEY', '')

        # Initialize Azure OpenAI async client
        try:
            self.client = AsyncAzureOpenAI(
                azure_endpoint=self.endpoint,
                api_key=self.api_key,
                api_version='2024-08-01-preview'
            )
            logger.info(f'ChatHandler initialized with model: {self.model}')
            logger.info(f'Azure Search index: {self.search_index}')
        except Exception as e:
            logger.error(f'Failed to initialize ChatHandler: {e}')
            self.client = None

    async def get_response(self, message: str, history: list = None, session_id: str = 'default') -> dict:
        """Get a chat response from Azure OpenAI with RAG from Azure Search.

        Args:
            message: The user's message
            history: Optional conversation history as list of {role, content} dicts
            session_id: Session identifier (unused for now)

        Returns:
            dict with 'response' key containing the AI's reply
        """
        if not self.client:
            return {
                'response': "I apologize, but I'm having trouble connecting. Please try voice mode for immediate assistance.",
                'status': 'error',
                'error': 'Client not initialized'
            }

        try:
            # Build messages array with system prompt and history
            messages = [{'role': 'system', 'content': SYSTEM_PROMPT}]

            # Add conversation history if provided
            if history:
                for entry in history[-10:]:  # Last 10 messages for context
                    role = entry.get('role', 'user')
                    if role == 'ai':
                        role = 'assistant'
                    if role in ('user', 'assistant'):
                        messages.append({
                            'role': role,
                            'content': entry.get('content', '')
                        })

            # Add the current user message
            messages.append({'role': 'user', 'content': message})

            logger.info(f'Sending chat request with {len(messages)} messages')

            # Configure Azure Search as data source for RAG
            extra_body = None
            if self.search_endpoint and self.search_index and self.search_key:
                extra_body = {
                    'data_sources': [{
                        'type': 'azure_search',
                        'parameters': {
                            'endpoint': self.search_endpoint,
                            'index_name': self.search_index,
                            'authentication': {
                                'type': 'api_key',
                                'key': self.search_key
                            },
                            'query_type': 'semantic',
                            'semantic_configuration': 'default',
                            'top_n_documents': 5
                        }
                    }]
                }
                logger.info(f'Using Azure Search RAG with index: {self.search_index}')

            # Call Azure OpenAI with optional RAG
            if extra_body:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    max_tokens=500,
                    temperature=0.7,
                    extra_body=extra_body
                )
            else:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    max_tokens=500,
                    temperature=0.7
                )

            assistant_message = response.choices[0].message.content
            logger.info(f'Received response: {assistant_message[:100]}...')

            return {
                'response': assistant_message,
                'status': 'success'
            }

        except Exception as e:
            logger.exception(f'Error getting chat response: {e}')
            return {
                'response': "I apologize, but I'm having trouble connecting right now. Please try voice mode for immediate assistance.",
                'status': 'error',
                'error': str(e)
            }
