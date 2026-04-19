import js from '@eslint/js'
import vue from 'eslint-plugin-vue'

export default [
  js.configs.recommended,
  ...vue.configs['flat/recommended'],
  {
    files: ['static/js/**/*.js'],
    languageOptions: {
      globals: {
        window: 'readonly',
        document: 'readonly',
        fetch: 'readonly',
        console: 'readonly',
        FormData: 'readonly',
        EventSource: 'readonly',
        setTimeout: 'readonly',
        setInterval: 'readonly',
        clearInterval: 'readonly',
        FileReader: 'readonly',
        DOMParser: 'readonly',
        HTMLElement: 'readonly',
        MutationObserver: 'readonly',
        Vue: 'readonly',
        TextDecoder: 'readonly',
        Headers: 'readonly',
        localStorage: 'readonly',
        confirm: 'readonly',
        prompt: 'readonly',
      },
    },
    rules: {
      eqeqeq: 'error',
      'no-var': 'error',
      'prefer-const': 'error',
      'no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', caughtErrorsIgnorePattern: '^_' },
      ],
      curly: 'error',
      'no-throw-literal': 'error',
    },
  },
  {
    files: ['templates/**/*.html'],
  },
  { ignores: ['node_modules/'] },
]
