#!/usr/bin/env python3
"""Reconcile public Response Card events without opening a public Faculty route."""
import argparse
import json

from app import create_app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['sync'])
    parser.add_argument('--data-dir', help='Explicit development catalog directory')
    args = parser.parse_args()
    service = create_app(data_dir=args.data_dir).extensions['response_cards']
    result = service.sync()
    # RESPONSE-CARDS "SEATING CARDS CLOSE AFTER THE EXAM": the minute timer also closes cards whose exam day has ended.
    closed = service.close_finished_exams()
    if closed['closed'] or closed['complete'] or closed['errors']:
        result['closed_after_exam'] = {key: closed[key] for key in ('closed', 'complete', 'pending')}
        result['closed_after_exam']['errors'] = len(closed['errors'])
    # RESPONSE-CARDS "REMINDERS BEFORE THE EXAM": reminders 3, 2 and 1 days before; the Inbox worker sends them.
    reminders = service.mail.queue_reminders()
    if reminders['queued'] or reminders['no_email']:
        result['reminders'] = reminders
    print(json.dumps(result))


if __name__ == '__main__':
    main()
