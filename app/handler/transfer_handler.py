"""Handler for managing agent transfer operations."""

import logging
from datetime import datetime, timezone
from typing import Optional
from app.handler.acs_cosmos_client import get_container

logger = logging.getLogger(__name__)


class TransferHandler:
    """Manages the transfer of calls from AI to human agents."""

    def __init__(self, config: dict):
        self.config = config
        self.agent_phone = config.get('AGENT_PHONE_NUMBER', '')

    def generate_summary(self, messages: list) -> str:
        """Generate a conversation summary from message history.

        Args:
            messages: List of conversation messages with role and content

        Returns:
            A condensed summary of the conversation
        """
        if not messages:
            return 'No conversation history available.'

        summary_parts = []
        extracted_info = {
            'name': None,
            'policy_type': None,
            'issue': None
        }

        for msg in messages:
            role = msg.get('role', 'unknown')
            content = msg.get('content', '')

            if role == 'user':
                content_lower = content.lower()
                if 'my name is' in content_lower or 'i am' in content_lower:
                    extracted_info['name'] = content
                if any(word in content_lower for word in ['policy', 'insurance', 'coverage', 'claim']):
                    extracted_info['issue'] = content

        conversation_length = len(messages)
        user_messages = [m for m in messages if m.get('role') == 'user']

        summary_parts.append(f'Conversation length: {conversation_length} messages')

        if user_messages:
            last_user_msg = user_messages[-1].get('content', '')[:200]
            summary_parts.append(f'Last user message: {last_user_msg}')

        if extracted_info['name']:
            summary_parts.append(f'Customer mentioned: {extracted_info["name"][:100]}')

        if extracted_info['issue']:
            summary_parts.append(f'Main topic: {extracted_info["issue"][:200]}')

        return '\n'.join(summary_parts)

    async def initiate_transfer(
        self,
        session_id: str,
        reason: str,
        messages: list,
        phone_number: Optional[str] = None
    ) -> dict:
        """Initiate a transfer to a human agent.

        Args:
            session_id: The current session ID
            reason: Reason for the transfer request
            messages: Conversation history
            phone_number: Customer's phone number

        Returns:
            Transfer request details including status
        """
        logger.info(f'Initiating transfer for session {session_id}: {reason}')

        summary = self.generate_summary(messages)

        transfer_data = {
            'status': 'pending',
            'requested_at': datetime.now(timezone.utc).isoformat(),
            'reason': reason,
            'summary': summary,
            'agent_phone': self.agent_phone
        }

        conversation_data = [
            {
                'role': msg.get('role', 'unknown'),
                'content': msg.get('content', ''),
                'timestamp': msg.get('timestamp', datetime.now(timezone.utc).isoformat())
            }
            for msg in messages
        ]

        try:
            container = get_container()
            item_id = f'session-{session_id}'

            # Try to read existing item first
            try:
                item = container.read_item(item=item_id, partition_key=item_id)
                logger.info(f'Found existing session {session_id}')
            except Exception:
                # Item doesn't exist, create a new one
                logger.info(f'Session {session_id} not found, creating new')
                item = {
                    'id': item_id,
                    'meta': {
                        'session_id': session_id,
                        'created_at': datetime.now(timezone.utc).isoformat()
                    },
                    'applicant': {
                        'phone': phone_number or 'Unknown'
                    }
                }

            # Update with transfer data
            item['transfer'] = transfer_data
            item['conversation'] = conversation_data
            if 'meta' not in item:
                item['meta'] = {}
            item['meta']['status'] = 'Transfer Requested'
            item['meta']['last_updated'] = datetime.now(timezone.utc).isoformat()

            # Use upsert to handle both create and update cases
            container.upsert_item(body=item)
            logger.info(f'Saved transfer request for session {session_id}')

            return {
                'success': True,
                'session_id': session_id,
                'transfer': transfer_data
            }

        except Exception as e:
            logger.error(f'Failed to initiate transfer: {e}')
            return {
                'success': False,
                'session_id': session_id,
                'error': str(e)
            }

    async def update_transfer_status(
        self,
        session_id: str,
        status: str,
        additional_info: Optional[dict] = None
    ) -> bool:
        """Update the status of an ongoing transfer.

        Args:
            session_id: The session ID
            status: New status (pending, in_progress, completed, failed)
            additional_info: Optional additional data to store

        Returns:
            True if update successful, False otherwise
        """
        logger.info(f'Updating transfer status for {session_id} to {status}')

        try:
            container = get_container()
            item = container.read_item(item=f'session-{session_id}', partition_key=f'session-{session_id}')

            if 'transfer' not in item:
                item['transfer'] = {}

            item['transfer']['status'] = status
            item['transfer']['updated_at'] = datetime.now(timezone.utc).isoformat()

            if additional_info:
                item['transfer'].update(additional_info)

            if status == 'completed':
                item['meta']['status'] = 'Transfer Complete'
            elif status == 'failed':
                item['meta']['status'] = 'Transfer Failed'
            elif status == 'in_progress':
                item['meta']['status'] = 'Transfer In Progress'

            container.replace_item(item=item, body=item)
            logger.info(f'Successfully updated transfer status for {session_id}')
            return True

        except Exception as e:
            logger.error(f'Failed to update transfer status: {e}')
            return False

    async def get_pending_transfers(self) -> list:
        """Get all pending transfer requests.

        Returns:
            List of pending transfer records
        """
        try:
            container = get_container()

            query = """
                SELECT c.id, c.meta, c.applicant, c.transfer, c.conversation
                FROM c
                WHERE c.transfer.status = 'pending' OR c.transfer.status = 'in_progress'
                ORDER BY c.transfer.requested_at DESC
            """

            items = list(container.query_items(query=query, enable_cross_partition_query=True))
            logger.info(f'Found {len(items)} pending transfers')
            return items

        except Exception as e:
            logger.error(f'Failed to get pending transfers: {e}')
            return []

    async def get_transfer_details(self, session_id: str) -> Optional[dict]:
        """Get full details for a specific transfer.

        Args:
            session_id: The session ID

        Returns:
            Full session document with transfer details, or None
        """
        try:
            container = get_container()
            item = container.read_item(item=f'session-{session_id}', partition_key=f'session-{session_id}')
            return item
        except Exception as e:
            logger.error(f'Failed to get transfer details for {session_id}: {e}')
            return None
