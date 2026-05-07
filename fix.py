import codecs

path = r'd:\project\parfume_rose\handlers\user_handlers.py'
with codecs.open(path, 'r', 'utf-8') as f:
    text = f.read()

new_text = text.replace(
    'async def universal_start(message: types.Message, state: FSMContext):\n    await state.finish()',
    'async def universal_start(message: types.Message, state: FSMContext):\n    import logging\n    logging.info(f"RECEIVED /start from user: {message.from_user.id}")\n    await state.finish()'
)

with codecs.open(path, 'w', 'utf-8') as f:
    f.write(new_text)
