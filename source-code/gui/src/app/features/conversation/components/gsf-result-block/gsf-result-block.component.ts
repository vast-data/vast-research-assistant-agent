import { Component, Input, signal, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatIcon } from '@angular/material/icon';
import { GsfResultBlock } from '../../../reports/services/session.mapper';

@Component({
    selector: 'app-gsf-result-block',
    standalone: true,
    imports: [CommonModule, MatIcon],
    templateUrl: './gsf-result-block.component.html',
    styleUrl: './gsf-result-block.component.scss',
    changeDetection: ChangeDetectionStrategy.OnPush
})
export class GsfResultBlockComponent {
    @Input({ required: true }) block!: GsfResultBlock;

    sqlExpanded = signal(false);
    tableExpanded = signal(true);

    toggleSql(): void {
        this.sqlExpanded.update(v => !v);
    }

    toggleTable(): void {
        this.tableExpanded.update(v => !v);
    }

    isActiveStep(index: number): boolean {
        return this.block.status === 'loading' && index === (this.block.steps?.length ?? 0) - 1;
    }

    get rowCount(): number {
        return this.block.results?.length ?? 0;
    }

    get displayColumns(): string[] {
        return this.block.columns ?? [];
    }

    get displayRows(): Record<string, unknown>[] {
        return this.block.results ?? [];
    }

    getCellValue(row: Record<string, unknown>, col: string): string {
        const val = row[col];
        if (val === null || val === undefined) return '—';
        if (typeof val === 'number') {
            return Number.isInteger(val) ? val.toString() : val.toFixed(2);
        }
        return String(val);
    }
}
