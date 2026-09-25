-- Minimal development seed. Replace demo content before production use.
BEGIN;

INSERT INTO permissions(code, description) VALUES
 ('student.read','View student records'),
 ('student.write','Manage student records'),
 ('question.read','View questions'),
 ('question.write','Create/edit questions'),
 ('question.publish','Publish questions'),
 ('test.read','View tests'),
 ('test.write','Create/edit tests'),
 ('test.publish','Publish tests'),
 ('content.write','Manage CMS content'),
 ('content.publish','Publish CMS content'),
 ('payment.read','View payments'),
 ('payment.refund','Process refunds'),
 ('subscription.write','Manage plans/subscriptions'),
 ('notification.write','Create notifications'),
 ('report.read','View reports'),
 ('settings.write','Manage settings')
ON CONFLICT (code) DO NOTHING;

INSERT INTO subjects(name, slug, display_order) VALUES
 ('Indian Polity','indian-polity',1),
 ('Economy','economy',2),
 ('History','history',3),
 ('Geography','geography',4),
 ('Environment','environment',5),
 ('Science & Technology','science-technology',6),
 ('Current Affairs','current-affairs',7),
 ('CSAT','csat',8)
ON CONFLICT (slug) DO NOTHING;

INSERT INTO plans(name, slug, price_paise, validity_days, is_public) VALUES
 ('Prelims Starter','prelims-starter',49900,180,true),
 ('Prelims Complete','prelims-complete',99900,365,true),
 ('Prelims + Current Affairs','prelims-current-affairs',149900,365,true)
ON CONFLICT (slug) DO NOTHING;

COMMIT;
