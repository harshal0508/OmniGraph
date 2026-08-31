# case_28_tablename_aliasing.py
class LegacyClientRecord:
    __tablename__ = 'users'

def update_client():
    LegacyClientRecord.objects.update(status='active')
