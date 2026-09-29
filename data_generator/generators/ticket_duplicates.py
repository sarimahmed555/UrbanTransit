"""Bounded streaming duplicate representations; never new passenger movements."""


def duplicate_budget(config):
    total = 1 if config.is_smoke else config.target_scale['raw_duplicate_ticket_copies']
    controlled = int(config.inject_quality_defects)
    return {'total': total if not config.is_smoke else controlled,
            'controlled_dq04': controlled,
            'streamed': max(0, total - controlled) if not config.is_smoke else 0}


class TicketDuplicateInjector:
    """Copy the first N base tickets once; preserve business data exactly.

    Only one ticket is held at a time. The caller streams the returned physical
    source mapping to a private audit CSV, separately from model feature roots.
    """
    def __init__(self, config, emit_audit):
        self.budget = duplicate_budget(config)
        self.emit_audit = emit_audit
        self.emitted = 0

    def observe(self, row, survivor_source_id, writer):
        if self.emitted >= self.budget['streamed']:
            return
        copy_source_id = writer.write(dict(row))
        self.emitted += 1
        self.emit_audit({'injection_id': f'PROD-DQ04-{self.emitted:05d}',
                         'rule_id': 'DQ04', 'ticket_id': row['ticket_id'],
                         'survivor_source_row_id': survivor_source_id,
                         'source_row_id': copy_source_id,
                         'expected_disposition': 'DUPLICATE_REMOVED',
                         'canonical_movement_increment': 0})

    def finish(self):
        if self.emitted != self.budget['streamed']:
            raise RuntimeError(f'duplicate budget incomplete: {self.emitted}/{self.budget["streamed"]}')
        return {**self.budget, 'streamed_emitted': self.emitted,
                'provenance_file': 'metadata/production_ticket_duplicates.csv'}
