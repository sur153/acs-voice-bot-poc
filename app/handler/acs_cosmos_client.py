from azure.cosmos import CosmosClient, exceptions
import os
import logging
from datetime import datetime, timezone
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

COSMOS_ENDPOINT = os.getenv("COSMOS_ENDPOINT")
COSMOS_KEY = os.getenv("COSMOS_KEY")
DATABASE_NAME = os.getenv("DATABASE_NAME")
CONTAINER_NAME = os.getenv("CONTAINER_NAME")

# Create client
client = CosmosClient(COSMOS_ENDPOINT, COSMOS_KEY)

def get_container():
    """
    Returns a Cosmos DB container client
    """
    client = CosmosClient(
        url=COSMOS_ENDPOINT,
        credential=COSMOS_KEY
    )

    database = client.get_database_client(DATABASE_NAME)
    container = database.get_container_client(CONTAINER_NAME)

    return container


def update_session_transcript(session_id: str, messages: list) -> bool:
    """Update the conversation transcript for a session.

    Args:
        session_id: The session ID
        messages: List of conversation messages

    Returns:
        True if successful, False otherwise
    """
    try:
        container = get_container()
        item_id = f'session-{session_id}'

        try:
            item = container.read_item(item=item_id, partition_key=item_id)
        except exceptions.CosmosResourceNotFoundError:
            logger.warning(f'Session {session_id} not found, creating new')
            item = {
                'id': item_id,
                'meta': {
                    'session_id': session_id,
                    'created_at': datetime.now(timezone.utc).isoformat()
                }
            }

        conversation_data = [
            {
                'role': msg.get('role', 'unknown'),
                'content': msg.get('content', ''),
                'timestamp': msg.get('timestamp', datetime.now(timezone.utc).isoformat())
            }
            for msg in messages
        ]

        item['conversation'] = conversation_data
        item['meta']['last_updated'] = datetime.now(timezone.utc).isoformat()

        container.upsert_item(body=item)
        logger.info(f'Updated transcript for session {session_id}')
        return True

    except Exception as e:
        logger.error(f'Failed to update transcript: {e}')
        return False


def create_transfer_request(
    session_id: str,
    summary: str,
    reason: str,
    agent_phone: str,
    phone_number: Optional[str] = None
) -> dict:
    """Create a transfer request record.

    Args:
        session_id: The session ID
        summary: Conversation summary
        reason: Reason for transfer
        agent_phone: Target agent phone number
        phone_number: Customer's phone number

    Returns:
        The created/updated document
    """
    try:
        container = get_container()
        item_id = f'session-{session_id}'

        try:
            item = container.read_item(item=item_id, partition_key=item_id)
        except exceptions.CosmosResourceNotFoundError:
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

        item['transfer'] = {
            'status': 'pending',
            'requested_at': datetime.now(timezone.utc).isoformat(),
            'reason': reason,
            'summary': summary,
            'agent_phone': agent_phone
        }

        item['meta']['status'] = 'Transfer Requested'
        item['meta']['last_updated'] = datetime.now(timezone.utc).isoformat()

        container.upsert_item(body=item)
        logger.info(f'Created transfer request for session {session_id}')
        return item

    except Exception as e:
        logger.error(f'Failed to create transfer request: {e}')
        raise


def get_pending_transfers() -> list:
    """Get all pending and in-progress transfer requests.

    Returns:
        List of transfer documents
    """
    try:
        container = get_container()

        query = """
            SELECT c.id, c.meta, c.applicant, c.transfer, c.conversation
            FROM c
            WHERE c.transfer.status = 'pending'
               OR c.transfer.status = 'in_progress'
            ORDER BY c.transfer.requested_at DESC
        """

        items = list(container.query_items(
            query=query,
            enable_cross_partition_query=True
        ))

        logger.info(f'Found {len(items)} pending transfers')
        return items

    except Exception as e:
        logger.error(f'Failed to get pending transfers: {e}')
        return []


def get_transfer_by_session(session_id: str) -> Optional[dict]:
    """Get transfer details for a specific session.

    Args:
        session_id: The session ID

    Returns:
        The session document or None
    """
    try:
        container = get_container()
        item_id = f'session-{session_id}'
        item = container.read_item(item=item_id, partition_key=item_id)
        return item

    except exceptions.CosmosResourceNotFoundError:
        logger.warning(f'Session {session_id} not found')
        return None

    except Exception as e:
        logger.error(f'Failed to get transfer: {e}')
        return None


def get_mcp_conversation_data(session_id: str) -> Optional[dict]:
    """Get MCP-collected conversation data from query_response container.

    The MCP server creates a new document for each answer, so we need to
    merge all documents for the same session to get the complete picture.

    Args:
        session_id: The Voice Live session ID (e.g., 'sess_xxx')

    Returns:
        Merged conversation_data object with all responses accumulated
    """
    try:
        client = CosmosClient(
            url=COSMOS_ENDPOINT,
            credential=COSMOS_KEY
        )
        database = client.get_database_client(DATABASE_NAME)
        container = database.get_container_client('query_response')

        # Get ALL documents for this session (MCP creates one per answer)
        # Order by timestamp ASC to process in chronological order
        query = """
            SELECT *
            FROM c
            WHERE c.conversation_data.id = @session_id
               OR c.conversation_data.meta.session_id = @session_id
            ORDER BY c._ts ASC
        """
        parameters = [{'name': '@session_id', 'value': session_id}]

        items = list(container.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True
        ))

        if not items:
            logger.info(f'No MCP conversation data found for session {session_id}')
            return None

        logger.info(f'Found {len(items)} MCP documents for session {session_id}, merging...')

        # Merge all documents into one combined result
        merged = {
            'meta': {'session_id': session_id},
            'applicant': {},
            'consent': {},
            'responses': {},
            'navigation_path': []
        }

        for doc in items:
            outer = doc.get('conversation_data', {})
            inner = outer.get('conversation_data', {}) if 'conversation_data' in outer else outer

            # Merge meta (later values override earlier)
            if inner.get('meta'):
                merged['meta'].update(inner['meta'])

            # Merge applicant info
            if inner.get('applicant'):
                merged['applicant'].update(inner['applicant'])

            # Merge consent info
            if inner.get('consent'):
                merged['consent'].update(inner['consent'])

            # Merge responses by category (accumulate all answers)
            if inner.get('responses'):
                for category, questions in inner['responses'].items():
                    if category == 'navigation_path':
                        # navigation_path might be in responses or at top level
                        continue
                    if isinstance(questions, dict):
                        if category not in merged['responses']:
                            merged['responses'][category] = {}
                        merged['responses'][category].update(questions)

            # Accumulate navigation_path (deduplicate)
            nav_path = inner.get('navigation_path') or inner.get('responses', {}).get('navigation_path', [])
            if nav_path:
                for q_id in nav_path:
                    if q_id not in merged['navigation_path']:
                        merged['navigation_path'].append(q_id)

        # Ensure session_id is set
        merged['meta']['session_id'] = session_id

        return merged

    except Exception as e:
        logger.error(f'Failed to get MCP conversation data: {e}')
        return None


def update_transfer_status(
    session_id: str,
    status: str,
    additional_info: Optional[dict] = None
) -> bool:
    """Update the status of a transfer.

    Args:
        session_id: The session ID
        status: New status (pending, in_progress, completed, failed)
        additional_info: Optional additional data

    Returns:
        True if successful, False otherwise
    """
    try:
        container = get_container()
        item_id = f'session-{session_id}'
        item = container.read_item(item=item_id, partition_key=item_id)

        if 'transfer' not in item:
            item['transfer'] = {}

        item['transfer']['status'] = status
        item['transfer']['updated_at'] = datetime.now(timezone.utc).isoformat()

        if additional_info:
            item['transfer'].update(additional_info)

        status_map = {
            'completed': 'Transfer Complete',
            'failed': 'Transfer Failed',
            'in_progress': 'Transfer In Progress',
            'pending': 'Transfer Requested'
        }
        item['meta']['status'] = status_map.get(status, item['meta'].get('status'))
        item['meta']['last_updated'] = datetime.now(timezone.utc).isoformat()

        container.replace_item(item=item, body=item)
        logger.info(f'Updated transfer status for {session_id} to {status}')
        return True

    except Exception as e:
        logger.error(f'Failed to update transfer status: {e}')
        return False


