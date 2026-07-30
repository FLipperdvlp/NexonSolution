from aiogram.fsm.state import State, StatesGroup

class CheckState(StatesGroup):
    waiting_for_username = State()

class ReviewState(StatesGroup):
    choosing_sign = State()
    waiting_target = State()
    waiting_description = State()
    waiting_photos = State()

class AdminState(StatesGroup):
    waiting_delreview_id = State()
    waiting_ban_target = State()
    waiting_unban_target = State()

class DepositState(StatesGroup):
    waiting_amount = State()

class WithdrawState(StatesGroup):
    waiting_amount = State()
    waiting_wallet = State()
    
class TransferState(StatesGroup):
    waiting_user = State()
    waiting_amount = State()