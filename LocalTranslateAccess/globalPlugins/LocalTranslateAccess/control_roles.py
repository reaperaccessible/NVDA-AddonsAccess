"""Expose the role of addon buttons consistently to NVDA."""
import weakref

_buttons=weakref.WeakValueDictionary()

def register_button(button):
    _buttons[button.GetHandle()]=button

def correct_button_role(obj):
    handle=getattr(obj,'windowHandle',None)
    button=_buttons.get(handle)
    if button is None:return False
    try:
        if not button or button.IsBeingDeleted() or button.GetHandle()!=handle:return False
    except RuntimeError:return False
    import controlTypes
    obj.role=controlTypes.Role.BUTTON
    return True
