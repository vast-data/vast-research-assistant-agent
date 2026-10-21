import { ChangeDetectionStrategy, Component, EventEmitter, inject, Input, OnInit, Output, signal } from '@angular/core';
import { MatIcon } from '@angular/material/icon';
import { MatTooltipModule } from '@angular/material/tooltip';
import { FormsModule } from '@angular/forms';
import { CollectionSelectorComponent } from "../../../conversation/components/collection-selector/collection-selector.component";
import { Collection } from '../../../shared/models/collections.model';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { SystemPromptService } from '../../../shared/services/system-prompt.service';
import { FEATURE_MODE_LABELS, FeatureMode, FeatureSettingsService } from '../../../shared/services/feature-settings.service';

@Component({
    selector: 'app-chat',
    standalone: true,
    imports: [
    MatIcon,
    MatTooltipModule,
    FormsModule,
    CollectionSelectorComponent,
    MatFormFieldModule,
    MatInputModule,
],
    templateUrl: './chat.component.html',
    styleUrl: './chat.component.scss',
    changeDetection: ChangeDetectionStrategy.OnPush
})
export class ChatComponent implements OnInit {
    promptService = inject(SystemPromptService);
    featureSettings = inject(FeatureSettingsService);

    @Input() createNewMode = false;
    @Input() transparentMode = false;
    @Output() submitMessage = new EventEmitter<{
        message: string;
        deepThinking: boolean;
        internetSearch: boolean;
        gsfEnabled: boolean;
        aidrEnabled: boolean;
        collections: Collection[];
        systemPrompt: string | null;
    }>();
    @Output() createNew = new EventEmitter<void>();

    focused = false;
    message = '';

    selectedCollections = signal<Collection[]>([]);

    readonly modes: { id: Exclude<FeatureMode, 'none'>; label: string; icon: string; tooltip: string }[] = [
        { id: 'internet', label: FEATURE_MODE_LABELS.internet, icon: 'language', tooltip: 'Search the internet for additional information' },
        { id: 'deep', label: FEATURE_MODE_LABELS.deep, icon: 'biotech', tooltip: 'Multi-step reasoning with multiple searches for thorough answers' },
        { id: 'gsf', label: FEATURE_MODE_LABELS.gsf, icon: 'storage', tooltip: 'Query structured EHR data through NVIDIA Auto-Ontology' },
        { id: 'aidr', label: FEATURE_MODE_LABELS.aidr, icon: 'shield', tooltip: 'Screen prompts and responses with AIDR guardrails' },
    ];

    setMode(mode: FeatureMode): void {
        this.featureSettings.setFeatureMode(mode);
        const next = this.featureSettings.featureMode();
        this.promptService.applyResearchMode(
            next === 'gsf' ? 'gsf' : next === 'deep' ? 'deep' : 'none'
        );
    }

    ngOnInit(): void {
        this.promptService.loadSystemPrompt();
        const mode = this.featureSettings.featureMode();
        this.promptService.applyResearchMode(
            mode === 'gsf' ? 'gsf' : mode === 'deep' ? 'deep' : 'none'
        );
    }

    onSubmit() {
        if (this.message && this.selectedCollections().length > 0) {
            const mode = this.featureSettings.featureMode();
            this.submitMessage.emit({
                message: this.message,
                deepThinking: mode === 'deep',
                internetSearch: mode === 'internet',
                gsfEnabled: mode === 'gsf',
                aidrEnabled: mode === 'aidr',
                collections: this.selectedCollections(),
                systemPrompt: this.promptService.getEffectivePrompt(mode === 'gsf', mode === 'deep'),
            });
            this.message = '';
        }
    }

    onCreateNew() {
        this.createNew.emit();
    }

    onCollectionsSelected(collections: Collection[]) {
        this.selectedCollections.set(collections);
    }
}
