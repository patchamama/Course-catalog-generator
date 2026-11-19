// ESLint flat config for frontend/assets/*.js
// These are browser scripts loaded via <script src="...">, not ES modules.
// catalog.js is always loaded first; its public functions become window globals
// available in chat.js, es_test.js, and HTML inline event handlers.

import js from '@eslint/js';
import globals from 'globals';

const catalogGlobals = {
  apiUrl: 'readonly', applyTheme: 'readonly', toggleTheme: 'readonly',
  openFModal: 'readonly', closeFModal: 'readonly',
  switchFModalTab: 'readonly', toggleFModalMaximize: 'readonly',
  openVideoModal: 'readonly', closeVideoModal: 'readonly',
  openYTModal: 'readonly', closeYTModal: 'readonly',
  fmodalSrtSeek: 'readonly', fmodalPlayVlc: 'readonly',
  escHtml: 'readonly', escapeHtml: 'readonly', unescapeContent: 'readonly',
  formatTime: 'readonly', getFileIcon: 'readonly',
  pushHistory: 'readonly', showHistory: 'readonly', hideHistory: 'readonly',
  EXT_TO_HLJS: 'readonly', FILE_TYPE_ICONS: 'readonly',
  openFileModal: 'readonly', doSearch: 'readonly', doAsk: 'readonly',
};

const cdnGlobals = { hljs: 'readonly', marked: 'readonly' };

const sharedRules = {
  'no-undef':             'error',
  'no-dupe-keys':         'error',
  'no-duplicate-case':    'error',
  'no-unreachable':       'warn',
  'no-use-before-define': ['warn', { functions: false, classes: true, variables: true }],
  'no-empty':             ['warn', { allowEmptyCatch: true }],
  'no-unused-vars':       ['warn', { vars: 'local', args: 'none',
                                     varsIgnorePattern: '^_', ignoreRestSiblings: true }],
  'eqeqeq':              ['warn', 'always', { null: 'ignore' }],
  'no-console':           'off',
  'no-var':               'off',
  'prefer-const':         'off',
  'no-redeclare':         'off',
};

export default [
  js.configs.recommended,
  {
    files: ['frontend/assets/catalog.js'],
    languageOptions: { ecmaVersion: 2022, sourceType: 'script',
                       globals: { ...globals.browser, ...cdnGlobals,
                                  // These are defined in es_test.js / chat.js and called
                                  // from catalog.js history handler (page-specific globals)
                                  doSearch: 'readonly', doAsk: 'readonly' } },
    rules: sharedRules,
  },
  {
    files: ['frontend/assets/chat.js'],
    languageOptions: { ecmaVersion: 2022, sourceType: 'script',
                       globals: { ...globals.browser, ...cdnGlobals, ...catalogGlobals } },
    rules: sharedRules,
  },
  {
    files: ['frontend/assets/es_test.js'],
    languageOptions: { ecmaVersion: 2022, sourceType: 'script',
                       globals: { ...globals.browser, ...cdnGlobals, ...catalogGlobals } },
    rules: {
      ...sharedRules,
      // es_test.js uses let variables (_backendOk, _idxEvt, _reindexEvt) declared at
      // file bottom but referenced in async callbacks defined earlier — intentional pattern
      'no-use-before-define': ['warn', { functions: false, classes: true, variables: false }],
    },
  },
];
